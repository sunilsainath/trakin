"""Invoice engine.

Two properties are non-negotiable and are enforced in the database, not here:

  * **Idempotence.** A billing run is keyed by (contract, direction, period,
    currency) with a unique index, so running it twice cannot produce a second
    invoice for the same period.
  * **Derivation.** Every total comes from contract terms, approved timesheets
    and line items. A client cannot supply an amount, and the database recomputes
    the header on every write.

The MSA gate is enforced here and again by a constraint: an invoice generated
without an active MSA is created as DRAFT and flagged `msa_required`, never
submitted.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.clock import utc_today
from app.core.errors import ResourceNotFoundError
from app.core.logging import get_logger
from app.db.session import session_scope
from app.services import audit

logger = get_logger(__name__)

MONEY = Decimal("0.0001")
ZERO = Decimal("0.0000")


def money(value: Any) -> Decimal:
    """Quantise to 4 decimal places, half-up. Never binary floating point."""
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


@dataclass(slots=True)
class LineDraft:
    contract_id: uuid.UUID
    description: str
    quantity: Decimal
    unit_rate: Decimal
    currency: str
    line_type: str = "TIMESHEET"
    source_line_item_id: uuid.UUID | None = None
    source_timesheet_id: uuid.UUID | None = None
    tax_rate: Decimal = ZERO
    service_period_start: dt.date | None = None
    service_period_end: dt.date | None = None


@dataclass(slots=True)
class InvoiceDraft:
    direction: str
    company_id: uuid.UUID
    contract_id: uuid.UUID
    counterparty_company_id: uuid.UUID | None
    counterparty_user_id: uuid.UUID | None
    period_start: dt.date
    period_end: dt.date
    currency: str
    lines: list[LineDraft] = field(default_factory=list)
    subtotal: Decimal = ZERO
    tax_total: Decimal = ZERO
    total: Decimal = ZERO
    public_id: str = ""
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "public_id": self.public_id,
            "direction": self.direction,
            "total": str(self.total),
            "currency": self.currency,
            "lines": len(self.lines),
        }


# ------------------------------------------------------------------- helpers
async def _contract_terms(conn: AsyncConnection, contract_id: uuid.UUID) -> dict[str, Any]:
    row = (
        (
            await conn.execute(
                text(
                    """
                SELECT c.id::text, c.public_id, c.project_id::text, c.sow_id::text,
                       c.company_id::text, c.counterparty_company_id::text,
                       c.counterparty_user_id::text, c.status, c.currency,
                       c.billing_basis::text, c.billing_frequency::text,
                       c.payment_terms_days, c.start_date, c.end_date
                  FROM public.contracts c
                 WHERE c.id = :id AND c.deleted_at IS NULL
                """
                ),
                {"id": contract_id},
            )
        )
        .mappings()
        .first()
    )

    if row is None:
        raise ResourceNotFoundError("Contract not found.")
    return dict(row)


async def _collect_timesheet_lines(
    conn: AsyncConnection,
    *,
    contract_id: uuid.UUID,
    period_start: dt.date,
    period_end: dt.date,
) -> list[LineDraft]:
    """One line per approved timesheet, traced back to its source sheet."""
    rows = (
        (
            await conn.execute(
                text(
                    """
                SELECT t.id::text AS timesheet_id, t.public_id, t.user_id::text,
                       t.billable_hours, t.total_amount, t.currency,
                       cr.role_title
                  FROM public.timesheets t
                  JOIN public.assignments a ON a.id = t.assignment_id
                  LEFT JOIN public.contract_roles cr
                         ON cr.id = t.contract_role_id
                 WHERE t.contract_id = :cid
                   AND t.status IN ('APPROVED', 'LOCKED')
                   AND t.period_start >= :start
                   AND t.period_end   <= :end
                   AND t.billable_hours > 0
                   AND NOT EXISTS (
                        SELECT 1 FROM public.invoice_items i
                         WHERE i.source_timesheet_id = t.id
                   )
                 ORDER BY t.period_start
                """
                ),
                {"cid": contract_id, "start": period_start, "end": period_end},
            )
        )
        .mappings()
        .all()
    )

    lines: list[LineDraft] = []
    for r in rows:
        hours = money(r["billable_hours"])
        amount = money(r["total_amount"])
        # Rate is derived from the amount and hours rather than trusted twice.
        rate = money(amount / hours) if hours > ZERO else ZERO

        lines.append(
            LineDraft(
                contract_id=contract_id,
                description=(
                    f"{r['role_title'] or 'Work'} — {r['public_id']} "
                    f"({r.get('period_start', period_start)} to {period_end})"
                ),
                quantity=hours,
                unit_rate=rate,
                currency=str(r["currency"]),
                line_type="TIMESHEET",
                source_timesheet_id=uuid.UUID(str(r["timesheet_id"])),
                service_period_start=period_start,
                service_period_end=period_end,
            )
        )
    return lines


async def _collect_recurring_lines(
    conn: AsyncConnection, contract_id: uuid.UUID
) -> list[LineDraft]:
    """Fixed, recurring and usage line items configured on the contract."""
    rows = (
        (
            await conn.execute(
                text(
                    """
                SELECT id::text, label, description, quantity, unit_rate, currency,
                       line_type::text, tax_rate::text, cap_amount,
                       billing_frequency::text
                  FROM public.contract_line_items
                 WHERE contract_id = :cid
                   AND is_active
                   AND line_type IN ('FIXED', 'RECURRING', 'USAGE')
                ORDER BY sort_order
                """
                ),
                {"cid": contract_id},
            )
        )
        .mappings()
        .all()
    )

    return [
        LineDraft(
            contract_id=contract_id,
            description=str(r["label"]),
            quantity=money(r["quantity"]),
            unit_rate=money(r["unit_rate"]),
            currency=str(r["currency"]),
            line_type=str(r["line_type"]),
            source_line_item_id=uuid.UUID(str(r["id"])),
            tax_rate=Decimal(str(r["tax_rate"])),
        )
        for r in rows
    ]


# ------------------------------------------------------------------ billing run
async def run_billing(idempotency_key: str) -> dict[str, Any]:
    """Execute a billing run identified by its idempotency key.

    Returns a summary; never raises for a partially-expected condition such as
    "nothing to bill", so a redelivered task converges instead of erroring.
    """
    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    async with session_scope() as conn:
        run = (
            (
                await conn.execute(
                    text(
                        """
                    SELECT id::text, public_id, company_id::text, period_start, period_end
                      FROM public.billing_runs
                     WHERE idempotency_key = :key OR public_id = :key
                     LIMIT 1
                    """
                    ),
                    {"key": idempotency_key},
                )
            )
            .mappings()
            .first()
        )

        if run is None:
            logger.warning("billing_run_not_found", key=idempotency_key)
            return {"created": [], "skipped": [], "status": "unknown_run"}

        await conn.execute(
            text(
                "UPDATE public.billing_runs SET status = 'RUNNING', started_at = now() "
                "WHERE id = CAST(:id AS uuid)"
            ),
            {"id": run["id"]},
        )

        contracts = (
            (
                await conn.execute(
                    text(
                        """
                    SELECT id::text, public_id
                      FROM public.contracts
                     WHERE company_id = CAST(:cid AS uuid)
                       AND deleted_at IS NULL
                       AND status IN ('ACCEPTED', 'ACTIVE')
                    """
                    ),
                    {"cid": run["company_id"]},
                )
            )
            .mappings()
            .all()
        )

        for contract in contracts:
            draft = await _build_invoice_draft(
                conn,
                contract_id=uuid.UUID(str(contract["id"])),
                period_start=run["period_start"] or _period_start_for(contract["public_id"]),
                period_end=run["period_end"] or utc_today(),
            )
            if draft is None or not draft.lines:
                skipped.append({"contract": contract["public_id"], "reason": "no_billable_lines"})
                continue

            result = await _persist_invoice(conn, draft=draft, run_id=str(run["id"]))
            (created if result["created"] else skipped).append(result)

        await conn.execute(
            text(
                """
                UPDATE public.billing_runs
                   SET status = :status, finished_at = now(),
                       invoices_created = :created, invoices_skipped = :skipped
                 WHERE id = CAST(:id AS uuid)
                """
            ),
            {
                "status": "PARTIALLY_FAILED" if not created else "SUCCEEDED",
                "created": len(created),
                "skipped": len(skipped),
                "id": run["id"],
            },
        )

    logger.info("billing_run_complete", created=len(created), skipped=len(skipped))
    return {"created": created, "skipped": skipped}


def _period_start_for(_contract_public_id: str) -> dt.date:
    """Default period: the current month. Overridden by the run's own dates."""
    today = utc_today()
    return today.replace(day=1)


def enqueue(billing_run_public_id: str, *, company_id: uuid.UUID | None = None) -> str:
    """Dispatch a billing run to a worker, or execute it inline.

    A run that is accepted and then never executes is worse than a slow request,
    so when no broker answers the work runs inline and the caller still gets a
    completed run. The chosen mode is logged.
    """
    from app.workers.celery_app import celery_app

    try:
        celery_app.send_task(
            "app.workers.tasks.generate_invoices",
            args=[billing_run_public_id],
            queue="bulk",
        )
    except Exception as exc:  # noqa: BLE001 - broker unavailable is expected locally
        logger.warning("billing_run_inline", run=billing_run_public_id, reason=str(exc)[:200])
        asyncio.run(run_billing(billing_run_public_id))
        return "inline"

    logger.info("billing_run_queued", run=billing_run_public_id)
    return "queued"


async def _build_invoice_draft(
    conn: AsyncConnection,
    *,
    contract_id: uuid.UUID,
    period_start: dt.date,
    period_end: dt.date,
) -> InvoiceDraft | None:
    terms = await _contract_terms(conn, contract_id)

    lines = await _collect_timesheet_lines(
        conn, contract_id=contract_id, period_start=period_start, period_end=period_end
    )
    lines.extend(await _collect_recurring_lines(conn, contract_id))

    if not lines:
        return None

    subtotal = ZERO
    tax_total = ZERO
    for line in lines:
        line_subtotal = money(line.quantity * line.unit_rate)
        line_tax = money(line_subtotal * line.tax_rate)
        subtotal += line_subtotal
        tax_total += line_tax

    total = money(subtotal + tax_total)
    currency = str(terms["currency"])
    warnings: list[str] = []

    # Currency consistency: a mixed-currency invoice would be an accounting error.
    mismatched = {line.currency for line in lines if line.currency != currency}
    if mismatched:
        warnings.append(f"Line items in {sorted(mismatched)} were not converted to {currency}.")

    return InvoiceDraft(
        direction="RECEIVABLE",
        company_id=uuid.UUID(str(terms["company_id"])),
        contract_id=contract_id,
        counterparty_company_id=(
            uuid.UUID(str(terms["counterparty_company_id"]))
            if terms["counterparty_company_id"]
            else None
        ),
        counterparty_user_id=(
            uuid.UUID(str(terms["counterparty_user_id"])) if terms["counterparty_user_id"] else None
        ),
        period_start=period_start,
        period_end=period_end,
        currency=currency,
        lines=lines,
        subtotal=money(subtotal),
        tax_total=money(tax_total),
        total=total,
        warnings=warnings,
    )


async def _persist_invoice(
    conn: AsyncConnection, *, draft: InvoiceDraft, run_id: str
) -> dict[str, Any]:
    """Write the invoice and its lines, or report that one already exists."""
    existing = (
        (
            await conn.execute(
                text(
                    """
                SELECT public_id FROM public.invoices
                 WHERE contract_id = :cid
                   AND direction = CAST(:dir AS public.invoice_direction)
                   AND period_start = :start
                   AND period_end = :end
                   AND currency = :ccy
                   AND deleted_at IS NULL
                """
                ),
                {
                    "cid": draft.contract_id,
                    "dir": draft.direction,
                    "start": draft.period_start,
                    "end": draft.period_end,
                    "ccy": draft.currency,
                },
            )
        )
        .mappings()
        .first()
    )

    if existing is not None:
        return {
            "created": False,
            "invoice": str(existing["public_id"]),
            "reason": "period_already_invoiced",
        }

    # MSA gate. Checked before insert so the flag is set correctly from the start,
    # and re-checked by a CHECK constraint on every later write.
    msa_ok = False
    if draft.counterparty_company_id is not None:
        msa_ok = bool(
            (
                await conn.execute(
                    text("SELECT app.has_active_msa(:a, :b) AS ok"),
                    {"a": draft.company_id, "b": draft.counterparty_company_id},
                )
            ).scalar()
        )

    terms = await _contract_terms(conn, draft.contract_id)
    payment_terms = int(terms["payment_terms_days"] or 30)
    issue_date = utc_today()
    due_date = issue_date + dt.timedelta(days=payment_terms)

    row = (
        (
            await conn.execute(
                text(
                    """
                INSERT INTO public.invoices
                  (public_id, billing_run_id, direction, company_id,
                   counterparty_company_id, counterparty_user_id, project_id, sow_id,
                   contract_id, period_start, period_end, issue_date, due_date,
                   currency, payment_terms_days, msa_required, msa_block_reason,
                   status, terms_snapshot, notes)
                VALUES
                  ('I' || upper(substr(md5(random()::text), 1, 8)), CAST(:run AS uuid),
                   CAST(:dir AS public.invoice_direction), CAST(:cid AS uuid),
                   CAST(:cp_company AS uuid), CAST(:cp_user AS uuid),
                   CAST(:project AS uuid), CAST(:sow AS uuid), CAST(:contract AS uuid),
                   :start, :end, :issue, :due, :ccy, :terms, :msa_required, :msa_reason,
                   'DRAFT', CAST(:snapshot AS jsonb), :notes)
                RETURNING public_id
                """
                ),
                {
                    "run": run_id,
                    "dir": draft.direction,
                    "cid": draft.company_id,
                    "cp_company": draft.counterparty_company_id,
                    "cp_user": draft.counterparty_user_id,
                    "project": terms["project_id"],
                    "sow": terms["sow_id"],
                    "contract": draft.contract_id,
                    "start": draft.period_start,
                    "end": draft.period_end,
                    "issue": issue_date,
                    "due": due_date,
                    "ccy": draft.currency,
                    "terms": payment_terms,
                    "msa_required": not msa_ok,
                    "msa_reason": (
                        None
                        if msa_ok
                        else "An active Master Service Agreement is required before submission."
                    ),
                    "snapshot": _json(
                        {
                            "billing_basis": terms["billing_basis"],
                            "billing_frequency": terms["billing_frequency"],
                            "payment_terms_days": payment_terms,
                            "currency": terms["currency"],
                        }
                    ),
                    "notes": "; ".join(draft.warnings) or None,
                },
            )
        )
        .mappings()
        .first()
    )

    invoice_id = (
        await conn.execute(
            text("SELECT id FROM public.invoices WHERE public_id = :pid"),
            {"pid": row["public_id"]},
        )
    ).scalar()

    for line in draft.lines:
        # Quantity, rate and tax rate are written as given; subtotals and tax are
        # derived by the database, so a client can never state its own total.
        await conn.execute(
            text(
                """
                INSERT INTO public.invoice_items
                  (invoice_id, contract_id, source_line_item_id, source_timesheet_id,
                   line_type, description, quantity, unit, unit_rate, tax_rate,
                   service_period_start, service_period_end, currency)
                VALUES
                  (CAST(:invoice AS uuid), CAST(:contract AS uuid), :src_item, :src_ts,
                   CAST(:ltype AS public.line_item_kind), :desc, :qty, :unit, :rate,
                   :tax, :ps, :pe, :ccy)
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "invoice": invoice_id,
                "contract": draft.contract_id,
                "src_item": line.source_line_item_id,
                "src_ts": line.source_timesheet_id,
                "ltype": line.line_type,
                "desc": line.description[:500],
                "qty": line.quantity,
                "unit": "HOUR" if line.line_type == "TIMESHEET" else "UNIT",
                "rate": line.unit_rate,
                "tax": line.tax_rate,
                "ps": line.service_period_start,
                "pe": line.service_period_end,
                "ccy": line.currency,
            },
        )

    draft.public_id = str(row["public_id"])

    await audit.record(
        conn,
        action="invoice.generated",
        resource_type="invoice",
        resource_id=uuid.UUID(str(invoice_id)),
        resource_public_id=str(row["public_id"]),
        company_id=draft.company_id,
        actor_type="SYSTEM",
        actor_label="billing_engine",
        new_values={
            "direction": draft.direction,
            "period_start": draft.period_start.isoformat(),
            "period_end": draft.period_end.isoformat(),
            "subtotal": str(draft.subtotal),
            "total": str(draft.total),
            "currency": draft.currency,
            "line_count": len(draft.lines),
            "msa_required": not msa_ok,
        },
        metadata={"warnings": draft.warnings},
    )

    logger.info(
        "invoice_generated",
        invoice_public_id=draft.public_id,
        total=str(draft.total),
        currency=draft.currency,
        msa_required=not msa_ok,
    )
    return {
        "created": True,
        "invoice": draft.public_id,
        "total": str(draft.total),
        "currency": draft.currency,
        "msa_required": not msa_ok,
    }


def _json(value: Any) -> str:
    import json

    return json.dumps(value, default=str)
