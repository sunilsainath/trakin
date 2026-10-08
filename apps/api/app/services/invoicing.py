"""BILLING: the server-side billing engine and the invoice lifecycle.

Money is computed in three independent places and never in the client:

  1. `app.compute_entry`      — an entry's rate snapshot and amount.
  2. `app.compute_invoice_item` — an item's subtotal, tax and total.
  3. `app.refresh_invoice_totals` — the invoice's subtotal/tax/total/balance.

This module *selects* what to bill (which approved timesheets, at which
contract-role rate) and then lets the database do the arithmetic. A request that
tries to set a total is discarded by `app.assert_invoice`, which recomputes from
`invoice_items` on every write.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.clock import utc_today
from app.core.errors import (
    BusinessRuleViolationError,
    InvalidStateTransitionError,
    ResourceNotFoundError,
    ValidationError,
)
from app.core.logging import get_logger
from app.services import audit
from app.services.code import _json
from app.services.lookup import as_decimal, resolve_scoped

logger = get_logger(__name__)

MONEY = Decimal("0.0001")
ZERO = Decimal("0")


def money(value: Any) -> Decimal:
    return Decimal(str(as_decimal(value))).quantize(MONEY, rounding=ROUND_HALF_UP)


_INVOICE_SELECT = """
    SELECT i.id, i.public_id, i.billing_run_id, i.direction, i.company_id,
           i.counterparty_company_id, i.counterparty_user_id, i.project_id, i.sow_id,
           i.contract_id, i.msa_id, i.invoice_number, i.status, i.period_start,
           i.period_end, i.issue_date, i.due_date, i.currency, i.subtotal, i.tax_total,
           i.total_amount, i.amount_paid, i.amount_disputed, i.balance_due,
           i.payment_terms_days, i.msa_required, i.msa_block_reason, i.disputed_reason,
           i.rejected_reason, i.notes, i.terms_snapshot, i.submitted_at, i.approved_at,
           i.paid_at, i.cancelled_at, i.document_id, i.locked, i.version,
           i.created_at, i.updated_at,
           c0.public_id AS company_public_id,
           c.public_id AS contract_public_id, c.title AS contract_title,
           p.public_id AS project_public_id, p.name AS project_name,
           s.public_id AS sow_public_id, s.title AS sow_title,
           cp.public_id AS counterparty_company_public_id,
           COALESCE(cp.display_name, cp.legal_name) AS counterparty_company_name,
           cu.public_id AS counterparty_user_public_id,
           NULLIF(TRIM(cu.first_name || ' ' || cu.last_name), '') AS counterparty_user_name,
           COALESCE(alloc.allocated, 0) AS allocated_total,
           COALESCE(items.item_count, 0)  AS item_count,
           COALESCE(roles.role_ids, '{}') AS role_ids
      FROM public.invoices i
      JOIN public.companies c0 ON c0.id = i.company_id
      JOIN public.contracts c ON c.id = i.contract_id
      LEFT JOIN public.projects p ON p.id = i.project_id
      LEFT JOIN public.sows s      ON s.id = i.sow_id
      LEFT JOIN public.companies cp ON cp.id = i.counterparty_company_id
      LEFT JOIN public.users cu     ON cu.id = i.counterparty_user_id
      LEFT JOIN LATERAL (
            SELECT sum(a.amount) AS allocated, count(*) AS item_count
              FROM public.invoice_allocations a WHERE a.invoice_id = i.id
      ) alloc ON TRUE
      LEFT JOIN LATERAL (
            SELECT count(*) AS item_count FROM public.invoice_items it WHERE it.invoice_id = i.id
      ) items ON TRUE
      LEFT JOIN LATERAL (
            SELECT array_agg(DISTINCT pr.public_id) AS role_ids
              FROM public.invoice_items it
              JOIN public.contract_roles cr ON cr.id = it.contract_role_id
              JOIN public.project_roles pr ON pr.id = cr.project_role_id
             WHERE it.invoice_id = i.id
      ) roles ON TRUE
"""

# Mirrors `app.assert_invoice`; published so the UI can enable only valid actions.
INVOICE_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "DRAFT": ("PENDING", "SUBMITTED", "CANCELLED"),
    "PENDING": ("DRAFT", "SUBMITTED", "CANCELLED"),
    "SUBMITTED": ("APPROVED", "REJECTED", "DISPUTED", "OVERDUE", "CANCELLED"),
    "APPROVED": ("PARTIALLY_PAID", "PAID", "OVERDUE", "DISPUTED", "CANCELLED"),
    "PARTIALLY_PAID": ("PAID", "OVERDUE", "DISPUTED", "REFUNDED", "CANCELLED"),
    "OVERDUE": ("PARTIALLY_PAID", "PAID", "DISPUTED", "CANCELLED"),
    "DISPUTED": ("APPROVED", "REJECTED", "CANCELLED"),
    "REJECTED": ("DRAFT", "CANCELLED"),
    "PAID": ("REFUNDED",),
    "CANCELLED": (),
    "REFUNDED": (),
}

OPEN_INVOICE_STATUSES = (
    "DRAFT",
    "PENDING",
    "SUBMITTED",
    "APPROVED",
    "PARTIALLY_PAID",
    "OVERDUE",
    "DISPUTED",
)


# =============================================================================
# reads
# =============================================================================
def _invoice_from_row(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["contract_id"] = data.pop("contract_public_id")
    data["project_id"] = data.pop("project_public_id", None)
    data["sow_id"] = data.pop("sow_public_id", None)
    data["company_id"] = data.pop("company_public_id")
    data["counterparty_company_id"] = data.pop("counterparty_company_public_id", None)
    data["counterparty_user_id"] = data.pop("counterparty_user_public_id", None)
    data.pop("contract_title", None)
    data.pop("project_name", None)
    data.pop("sow_title", None)
    data.pop("counterparty_company_name", None)
    data.pop("counterparty_user_name", None)
    data["role_ids"] = sorted(data.get("role_ids") or [])
    data["terms_snapshot"] = data.get("terms_snapshot") or {}
    return data


async def get_invoice(
    conn: AsyncConnection, *, company_id: uuid.UUID, public_id: str
) -> dict[str, Any]:
    row = (
        (
            await conn.execute(
                text(f"{_INVOICE_SELECT} WHERE i.public_id = :pid AND i.company_id = :cid"),
                {"pid": public_id, "cid": company_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ResourceNotFoundError("Invoice not found.")

    data = _invoice_from_row(row)
    data["items"] = await _invoice_items(conn, invoice_id=row["id"])
    data["allocations"] = (
        (
            await conn.execute(
                text(
                    """
                    SELECT a.source, a.amount, a.currency, a.allocated_at, a.note,
                           p.public_id AS payment_public_id, p.id::text AS payment_id
                      FROM public.invoice_allocations a
                      LEFT JOIN public.payments p ON p.id = a.payment_id
                     WHERE a.invoice_id = :iid
                     ORDER BY a.allocated_at DESC
                    """
                ),
                {"iid": row["id"]},
            )
        )
        .mappings()
        .all()
    )
    data["allocations"] = [dict(a) for a in data["allocations"]]
    data["approvals"] = (
        (
            await conn.execute(
                text(
                    """
                    SELECT step_no, name, status, notes, requested_at, decided_at
                      FROM public.invoice_approvals WHERE invoice_id = :iid ORDER BY step_no
                    """
                ),
                {"iid": row["id"]},
            )
        )
        .mappings()
        .all()
    )
    data["approvals"] = [dict(a) for a in data["approvals"]]
    data["allowed_transitions"] = list(INVOICE_TRANSITIONS.get(str(row["status"]), ()))
    data["history"] = await _invoice_history(conn, row["public_id"], company_id)
    return data


async def _invoice_items(conn: AsyncConnection, *, invoice_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        (
            await conn.execute(
                text(
                    """
                    SELECT it.id::text, it.description, it.line_type, it.quantity, it.unit,
                           it.unit_rate, it.subtotal, it.tax_rate, it.tax_total, it.total,
                           it.currency, it.service_period_start, it.service_period_end,
                           it.source_timesheet_id::text AS source_timesheet_id,
                           pr.public_id AS project_role_id, pr.title AS project_role_title
                      FROM public.invoice_items it
                      LEFT JOIN public.contract_roles cr ON cr.id = it.contract_role_id
                      LEFT JOIN public.project_roles pr ON pr.id = cr.project_role_id
                     WHERE it.invoice_id = :iid
                     ORDER BY it.id
                    """
                ),
                {"iid": invoice_id},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def _invoice_history(
    conn: AsyncConnection, public_id: str, company_id: uuid.UUID
) -> list[dict[str, Any]]:
    rows = (
        (
            await conn.execute(
                text(
                    """
                    SELECT a.action, a.new_values, a.reason, a.occurred_at AS created_at,
                           u.public_id AS actor_public_id,
                           NULLIF(TRIM(u.first_name || ' ' || u.last_name), '') AS actor_name
                      FROM platform.audit_logs a
                      LEFT JOIN public.users u ON u.id = a.actor_user_id
                     WHERE a.resource_type = 'invoice'
                       AND a.resource_public_id = :pid
                       AND a.company_id = :cid
                     ORDER BY a.occurred_at DESC LIMIT 100
                    """
                ),
                {"pid": public_id, "cid": company_id},
            )
        )
        .mappings()
        .all()
    )
    from app.services.lookup import json_or_empty

    return [
        {
            "action": r["action"],
            "at": r["created_at"],
            "actor_public_id": r["actor_public_id"],
            "actor_name": r["actor_name"],
            "reason": r["reason"],
            "changes": json_or_empty(r["new_values"]),
        }
        for r in rows
    ]


async def list_invoices(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    direction: str = "RECEIVABLE",
    status: str | None = None,
    statuses: list[str] | None = None,
    project_public_id: str | None = None,
    contract_public_id: str | None = None,
    counterparty_company_id: str | None = None,
    overdue_only: bool = False,
    period_start: date | None = None,
    period_end: date | None = None,
    search: str | None = None,
    limit: int,
    cursor_keys: dict[str, str],
) -> list[dict[str, Any]]:
    if direction not in ("RECEIVABLE", "PAYABLE"):
        raise ValidationError(
            "Direction must be RECEIVABLE or PAYABLE.", details={"field": "direction"}
        )
    where = ["i.company_id = :cid", "i.deleted_at IS NULL", "i.direction = :direction"]
    params: dict[str, Any] = {"cid": company_id, "direction": direction, "limit": limit + 1}

    if status:
        where.append("i.status = :status")
        params["status"] = status
    if statuses:
        where.append("i.status = ANY(CAST(:statuses AS text[]))")
        params["statuses"] = statuses
    if project_public_id:
        project = await resolve_scoped(conn, "projects", project_public_id, company_id)
        where.append("i.project_id = :pid")
        params["pid"] = project["id"]
    if contract_public_id:
        contract = await resolve_scoped(conn, "contracts", contract_public_id, company_id)
        where.append("i.contract_id = :contract")
        params["contract"] = contract["id"]
    if counterparty_company_id:
        where.append("i.counterparty_company_id = :cpid")
        params["cpid"] = counterparty_company_id
    if overdue_only:
        where.append(
            "i.status IN ('APPROVED','PARTIALLY_PAID','OVERDUE') AND i.balance_due > 0"
            " AND i.due_date < current_date"
        )
    if period_start:
        where.append("i.period_end >= :period_start")
        params["period_start"] = period_start
    if period_end:
        where.append("i.period_start <= :period_end")
        params["period_end"] = period_end
    if search:
        where.append("(i.invoice_number ILIKE :q OR c.title ILIKE :q)")
        params["q"] = f"%{search}%"
    if cursor_keys.get("created_at"):
        where.append("(i.created_at, i.public_id) < (:cur_created, :cur_public)")
        params["cur_created"] = cursor_keys["created_at"]
        params["cur_public"] = cursor_keys["public_id"]

    rows = (
        (
            await conn.execute(
                text(
                    f"""
                    {_INVOICE_SELECT}
                     WHERE {" AND ".join(where)}
                     ORDER BY i.created_at DESC, i.public_id DESC
                     LIMIT :limit
                    """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    return [_invoice_from_row(r) for r in rows]


# =============================================================================
# billing engine
# =============================================================================
async def billable_timesheets(
    conn: AsyncConnection,
    *,
    contract_id: uuid.UUID,
    period_start: date,
    period_end: date,
) -> list[dict[str, Any]]:
    """Approved or locked timesheets in the period that are not yet invoiced.

    Rule 3: only approved/locked sheets are billable, and the unique index
    `ux_invoice_item_timesheet` guarantees an approved sheet is invoiced once.
    """
    rows = (
        (
            await conn.execute(
                text(
                    """
                    SELECT t.id::text, t.public_id, t.user_id, t.total_hours, t.billable_hours,
                           t.total_amount, t.currency, t.period_start, t.period_end,
                           t.contract_role_id,
                           pr.public_id AS role_public_id, pr.title AS role_title,
                           cr.rate, cr.currency AS rate_currency, cr.rate_type,
                           NULLIF(TRIM(u.first_name || ' ' || u.last_name), '') AS user_name,
                           u.public_id AS user_public_id
                      FROM public.timesheets t
                      JOIN public.users u ON u.id = t.user_id
                      LEFT JOIN public.contract_roles cr ON cr.id = t.contract_role_id
                      LEFT JOIN public.project_roles pr ON pr.id = cr.project_role_id
                     WHERE t.contract_id = :cid
                       AND t.status IN ('APPROVED','LOCKED')
                       AND t.period_start >= :period_start
                       AND t.period_end <= :period_end
                       AND NOT EXISTS (
                             SELECT 1 FROM public.invoice_items it
                              WHERE it.source_timesheet_id = t.id
                       )
                     ORDER BY t.period_start, t.public_id
                    """
                ),
                {"cid": contract_id, "period_start": period_start, "period_end": period_end},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def recurring_line_items(
    conn: AsyncConnection, *, contract_id: uuid.UUID, period_start: date, period_end: date
) -> list[dict[str, Any]]:
    """Fixed, recurring, usage and milestone lines active in the period."""
    rows = (
        (
            await conn.execute(
                text(
                    """
                    SELECT id::text, label, description, line_type, quantity, unit,
                           unit_rate, amount, currency, tax_rate, is_taxable, is_additional,
                           billing_basis, billing_frequency
                      FROM public.contract_line_items
                     WHERE contract_id = :cid
                       AND is_active
                       AND line_type IN ('FIXED','RECURRING','USAGE','MILESTONE')
                       AND (proration_start IS NULL OR proration_start <= :period_end)
                       AND (proration_end IS NULL OR proration_end >= :period_start)
                     ORDER BY sort_order, id
                    """
                ),
                {"cid": contract_id, "period_start": period_start, "period_end": period_end},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


def _effective_rate(sheet: dict[str, Any]) -> Decimal:
    """Rate for a timesheet line: the contract role rate, never the request."""
    rate = sheet.get("rate")
    if rate is not None:
        return money(rate)
    hours = as_decimal(sheet.get("billable_hours"))
    amount = as_decimal(sheet.get("total_amount"))
    if hours > 0:
        return money(amount / hours)
    return money(0)


async def preview_invoice(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    contract_public_id: str,
    period_start: date,
    period_end: date,
) -> dict[str, Any]:
    """Compute what an invoice for this period would contain.

    The preview and the persisted invoice run the *same* calculation, so what an
    approver sees is what gets issued.
    """
    contract = await resolve_scoped(conn, "contracts", contract_public_id, company_id)
    if str(contract["status"]) not in {"ACCEPTED", "ACTIVE"}:
        raise BusinessRuleViolationError(
            "Only an accepted or active contract can be billed.",
            details={"reason": "CONTRACT_NOT_BILLABLE", "status": contract["status"]},
        )
    if period_end < period_start:
        raise ValidationError("The billing period end must not precede the start.")

    sheets = await billable_timesheets(
        conn, contract_id=contract["id"], period_start=period_start, period_end=period_end
    )
    lines = await recurring_line_items(
        conn, contract_id=contract["id"], period_start=period_start, period_end=period_end
    )

    items: list[dict[str, Any]] = []
    subtotal = ZERO
    tax_total = ZERO

    for sheet in sheets:
        hours = as_decimal(sheet["billable_hours"])
        rate = _effective_rate(sheet)
        line_subtotal = money(hours * rate)
        items.append(
            {
                "line_type": "TIMESHEET",
                "description": (
                    f"{sheet['role_title'] or 'Contracted role'} — "
                    f"{sheet['period_start']} to {sheet['period_end']}"
                    f" ({sheet['user_name'] or 'Resource'})"
                ),
                "quantity": hours,
                "unit": "HOUR",
                "unit_rate": rate,
                "tax_rate": ZERO,
                "currency": sheet["rate_currency"] or contract["currency"],
                "source_timesheet_id": sheet["id"],
                "source_timesheet_public_id": sheet["public_id"],
                "project_role_id": sheet["role_public_id"],
                "service_period_start": sheet["period_start"],
                "service_period_end": sheet["period_end"],
            }
        )
        subtotal += line_subtotal

    for line in lines:
        line_subtotal = money(as_decimal(line["amount"]))
        effective_tax_rate = line["tax_rate"] if line["is_taxable"] else ZERO
        items.append(
            {
                "line_type": line["line_type"],
                "description": line["description"] or line["label"],
                "quantity": line["quantity"],
                "unit": line["unit"],
                "unit_rate": line["unit_rate"],
                "tax_rate": effective_tax_rate,
                "currency": line["currency"],
                "source_line_item_id": line["id"],
                "service_period_start": period_start,
                "service_period_end": period_end,
            }
        )
        subtotal += line_subtotal
        tax_total += money(line_subtotal * effective_tax_rate)

    warnings: list[str] = []
    if not items:
        warnings.append("No approved timesheets or billable lines in this period.")
    for item in items:
        if item["currency"] != contract["currency"]:
            warnings.append(
                f"Line '{item['description']}' is in {item['currency']} while the contract is in "
                f"{contract['currency']}."
            )

    return {
        "contract_id": contract_public_id,
        "contract_status": contract["status"],
        "period_start": period_start,
        "period_end": period_end,
        "currency": contract["currency"],
        "subtotal": money(subtotal),
        "tax_total": money(tax_total),
        "total": money(subtotal + tax_total),
        "items": items,
        "warnings": warnings,
        "msa_required": bool(contract.get("counterparty_company_id")),
    }


async def generate_invoice(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    contract_public_id: str,
    period_start: date,
    period_end: date,
    billing_run_id: uuid.UUID | None = None,
    discount: Decimal = ZERO,
    adjustment: Decimal = ZERO,
    notes: str | None = None,
) -> dict[str, Any]:
    """Turn billable work into a DRAFT invoice.

    Idempotent per (contract, direction, period, currency): the unique index
    `ux_invoices_period` rejects a duplicate, so a retried billing run returns the
    invoice that already exists instead of double-billing the customer.
    """
    preview = await preview_invoice(
        conn,
        company_id=company_id,
        contract_public_id=contract_public_id,
        period_start=period_start,
        period_end=period_end,
    )
    contract = await resolve_scoped(conn, "contracts", contract_public_id, company_id)

    if not preview["items"]:
        raise BusinessRuleViolationError(
            "There is nothing billable in this period.",
            details={"reason": "NOTHING_TO_BILL"},
        )

    existing = await conn.execute(
        text(
            """
            SELECT public_id FROM public.invoices
             WHERE contract_id = :cid AND direction = 'RECEIVABLE'
               AND period_start = :start AND period_end = :end
               AND currency = :currency AND deleted_at IS NULL
            """
        ),
        {
            "cid": contract["id"],
            "start": period_start,
            "end": period_end,
            "currency": preview["currency"],
        },
    )
    duplicate = existing.mappings().first()
    if duplicate is not None:
        raise BusinessRuleViolationError(
            "An invoice already covers this contract and period.",
            details={
                "reason": "INVOICE_PERIOD_EXISTS",
                "invoice_id": str(duplicate["public_id"]),
            },
        )

    terms = int(contract["payment_terms_days"] or 30)
    issue_date = period_end
    due_date = issue_date + timedelta(days=terms)

    row = (
        (
            await conn.execute(
                text(
                    """
                    INSERT INTO public.invoices
                      (billing_run_id, direction, company_id, counterparty_company_id,
                       counterparty_user_id, project_id, sow_id, contract_id, status,
                       period_start, period_end, issue_date, due_date, currency,
                       payment_terms_days, notes, terms_snapshot, created_by)
                    VALUES
                      (:run, 'RECEIVABLE', :cid, :counterparty_company, :counterparty_user,
                       :pid, :sid, :contract, 'DRAFT', :start, :end, :issue, :due, :currency,
                       :terms, :notes, CAST(:snapshot AS jsonb), :actor)
                    RETURNING public_id
                    """
                ),
                {
                    "run": billing_run_id,
                    "cid": company_id,
                    "counterparty_company": contract["counterparty_company_id"],
                    "counterparty_user": contract["counterparty_user_id"],
                    "pid": contract["project_id"],
                    "sid": contract["sow_id"],
                    "contract": contract["id"],
                    "start": period_start,
                    "end": period_end,
                    "issue": issue_date,
                    "due": due_date,
                    "currency": preview["currency"],
                    "terms": terms,
                    "notes": notes,
                    "snapshot": _json(
                        {
                            "billing_basis": contract["billing_basis"],
                            "billing_frequency": contract["billing_frequency"],
                            "payment_terms_days": terms,
                            "discount": str(money(discount)),
                            "adjustment": str(money(adjustment)),
                            "generated_by": "billing_engine",
                        }
                    ),
                    "actor": actor_user_id,
                },
            )
        )
        .mappings()
        .first()
    )

    invoice = await resolve_scoped(conn, "invoices", str(row["public_id"]), company_id)

    for item in preview["items"]:
        await conn.execute(
            text(
                """
                INSERT INTO public.invoice_items
                  (invoice_id, contract_id, source_line_item_id, source_timesheet_id,
                   project_id, contract_role_id, line_type, description, quantity, unit,
                   unit_rate, tax_rate, currency, service_period_start, service_period_end)
                VALUES
                  (:invoice, :contract, CAST(:sli AS uuid), CAST(:sts AS uuid),
                   CAST(:project AS uuid), :crid, :line_type, :description, :quantity, :unit,
                   :unit_rate, :tax_rate, :currency, :period_start, :period_end)
                """
            ),
            {
                "invoice": invoice["id"],
                "contract": contract["id"],
                "sli": item.get("source_line_item_id"),
                "sts": item.get("source_timesheet_id"),
                "project": contract["project_id"],
                "crid": await _contract_role_id(
                    conn, contract_id=contract["id"], role_public_id=item.get("project_role_id")
                ),
                "line_type": item["line_type"],
                "description": item["description"][:2000],
                "quantity": item["quantity"],
                "unit": item["unit"],
                "unit_rate": item["unit_rate"],
                "tax_rate": item["tax_rate"],
                "currency": item["currency"],
                "period_start": item["service_period_start"],
                "period_end": item["service_period_end"],
            },
        )

    if discount or adjustment:
        await _apply_invoice_adjustment(
            conn,
            invoice_id=invoice["id"],
            contract_id=contract["id"],
            discount=money(discount),
            adjustment=money(adjustment),
            created_by=actor_user_id,
        )

    await audit.record(
        conn,
        action="invoice.generated",
        resource_type="invoice",
        resource_public_id=str(row["public_id"]),
        company_id=company_id,
        actor_user_id=actor_user_id,
        new_values={
            "contract_id": contract_public_id,
            "period_start": str(period_start),
            "period_end": str(period_end),
            "currency": preview["currency"],
            "item_count": len(preview["items"]),
        },
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_invoice(conn, company_id=company_id, public_id=str(row["public_id"]))


async def create_vendor_bill(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Record a vendor bill: money this company owes (PAYABLE).

    Unlike engine generation (which prices approved timesheets into
    RECEIVABLE invoices), a vendor bill is entered from the vendor's own
    paperwork: free-form line items under a contract for context. Amounts
    are still derived server-side by the invoice triggers, never trusted
    from the request. PAYABLE invoices are exempt from the MSA gate by
    design (ck_invoice_msa_gate): a company does not need an MSA to owe
    its vendors.
    """
    from app.services.lookup import resolve_company_public_id, resolve_user_public_id

    if not payload.get("contract_id"):
        raise ValidationError(
            "A vendor bill belongs to a contract.",
            details={"field": "contract_id"},
        )
    contract = await resolve_scoped(conn, "contracts", payload["contract_id"], company_id)

    counterparty_company = (
        await resolve_company_public_id(conn, payload["counterparty_company_id"])
        if payload.get("counterparty_company_id")
        else None
    )
    counterparty_user = (
        await resolve_user_public_id(conn, payload["counterparty_user_id"])
        if payload.get("counterparty_user_id")
        else None
    )
    if bool(counterparty_company) == bool(counterparty_user):
        raise ValidationError(
            "Name the vendor as a company or as a user, not both and not neither.",
            details={"reason": "COUNTERPARTY_REQUIRED"},
        )

    items = payload.get("items") or []
    if not items:
        raise ValidationError(
            "A vendor bill needs at least one line item.",
            details={"reason": "ITEMS_REQUIRED"},
        )
    if len(items) > 200:
        raise ValidationError(
            "A vendor bill holds at most 200 line items.",
            details={"reason": "TOO_MANY_ITEMS"},
        )

    issue_date = payload.get("issue_date") or utc_today().isoformat()
    due_date = payload.get("due_date")
    terms = int(payload.get("payment_terms_days", 30) or 30)
    if due_date is None:
        due_date = (date.fromisoformat(str(issue_date)) + timedelta(days=terms)).isoformat()

    row = (
        (
            await conn.execute(
                text(
                    """
                    INSERT INTO public.invoices
                      (direction, company_id, counterparty_company_id,
                       counterparty_user_id, project_id, sow_id, contract_id, status,
                       period_start, period_end, issue_date, due_date, currency,
                       payment_terms_days, notes, terms_snapshot, created_by)
                    VALUES
                      ('PAYABLE', :cid, :counterparty_company, :counterparty_user,
                       :pid, :sid, :contract, 'DRAFT',
                       :issue, :issue, :issue, :due, :currency,
                       :terms, :notes, CAST(:snapshot AS jsonb), :actor)
                    RETURNING public_id
                    """
                ),
                {
                    "cid": company_id,
                    "counterparty_company": counterparty_company,
                    "counterparty_user": counterparty_user,
                    "pid": contract["project_id"],
                    "sid": contract["sow_id"],
                    "contract": contract["id"],
                    "issue": issue_date,
                    "due": due_date,
                    "currency": payload.get("currency", "USD"),
                    "terms": terms,
                    "notes": payload.get("notes"),
                    "snapshot": _json({"source": "vendor_bill"}),
                    "actor": actor_user_id,
                },
            )
        )
        .mappings()
        .first()
    )
    invoice = await resolve_scoped(conn, "invoices", str(row["public_id"]), company_id)

    for position, item in enumerate(items):
        description = str(item.get("description") or "").strip()
        if not description:
            raise ValidationError(
                f"Line {position + 1} needs a description.",
                details={"reason": "ITEM_DESCRIPTION_REQUIRED", "position": position},
            )
        line_type = str(item.get("line_type") or "FIXED").upper()
        if line_type not in (
            "FIXED",
            "RECURRING",
            "USAGE",
            "MILESTONE",
            "TIMESHEET",
            "VARIABLE",
            "ADDITIONAL",
        ):
            raise ValidationError(
                f"Line {position + 1} has an unknown line type.",
                details={"reason": "ITEM_TYPE_UNKNOWN", "position": position},
            )
        quantity = as_decimal(item.get("quantity", 1))
        unit_rate = as_decimal(item.get("unit_rate", 0))
        if quantity <= 0 or unit_rate < 0:
            raise ValidationError(
                f"Line {position + 1} has an impossible quantity or rate.",
                details={"reason": "ITEM_AMOUNT_INVALID", "position": position},
            )
        await conn.execute(
            text(
                """
                INSERT INTO public.invoice_items
                  (invoice_id, contract_id, project_id, line_type, description,
                   quantity, unit, unit_rate, tax_rate, currency,
                   service_period_start, service_period_end)
                VALUES
                  (:invoice, :contract, :project, :line_type, :description,
                   :quantity, :unit, :unit_rate, :tax_rate, :currency,
                   :period, :period)
                """
            ),
            {
                "invoice": invoice["id"],
                "contract": contract["id"],
                "project": contract["project_id"],
                "line_type": line_type,
                "description": description[:2000],
                "quantity": quantity,
                "unit": str(item.get("unit") or "UNIT"),
                "unit_rate": unit_rate,
                "tax_rate": as_decimal(item.get("tax_rate", 0)),
                "currency": payload.get("currency", "USD"),
                "period": issue_date,
            },
        )

    await audit.record(
        conn,
        action="invoice.vendor_bill_created",
        resource_type="invoice",
        resource_public_id=str(row["public_id"]),
        company_id=company_id,
        actor_user_id=actor_user_id,
        new_values={
            "direction": "PAYABLE",
            "contract_id": payload["contract_id"],
            "item_count": len(items),
        },
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_invoice(conn, company_id=company_id, public_id=str(row["public_id"]))


async def _contract_role_id(
    conn: AsyncConnection, *, contract_id: uuid.UUID, role_public_id: str | None
) -> Any:
    if not role_public_id:
        return None
    row = (
        (
            await conn.execute(
                text(
                    """
                    SELECT cr.id::text FROM public.contract_roles cr
                      JOIN public.project_roles pr ON pr.id = cr.project_role_id
                     WHERE cr.contract_id = :cid AND pr.public_id = :pid
                    """
                ),
                {"cid": contract_id, "pid": role_public_id},
            )
        )
        .mappings()
        .first()
    )
    return row["id"] if row else None


async def _apply_invoice_adjustment(
    conn: AsyncConnection,
    *,
    invoice_id: uuid.UUID,
    contract_id: uuid.UUID,
    discount: Decimal,
    adjustment: Decimal,
    created_by: uuid.UUID,
) -> None:
    """Discount and adjustments are their own lines so the arithmetic is visible."""
    if discount > 0:
        await conn.execute(
            text(
                """
                INSERT INTO public.invoice_items
                  (invoice_id, contract_id, line_type, description, quantity, unit,
                   unit_rate, currency)
                VALUES (:invoice, :contract, 'VARIABLE', 'Discount', 1, 'LOT',
                        :amount, (SELECT currency FROM public.invoices WHERE id = :invoice))
                """
            ),
            {"invoice": invoice_id, "contract": contract_id, "amount": -discount},
        )
    if adjustment != 0:
        await conn.execute(
            text(
                """
                INSERT INTO public.invoice_items
                  (invoice_id, contract_id, line_type, description, quantity, unit,
                   unit_rate, currency)
                VALUES (:invoice, :contract, 'ADDITIONAL', 'Adjustment', 1, 'LOT',
                        :amount, (SELECT currency FROM public.invoices WHERE id = :invoice))
                """
            ),
            {"invoice": invoice_id, "contract": contract_id, "amount": adjustment},
        )


# =============================================================================
# invoice lifecycle
# =============================================================================
async def transition_invoice(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    target: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    reason: str | None = None,
) -> dict[str, Any]:
    before = await resolve_scoped(conn, "invoices", public_id, company_id, lock=True)
    current = str(before["status"])
    if target not in INVOICE_TRANSITIONS.get(current, ()):
        raise InvalidStateTransitionError(
            f"An invoice in status {current} cannot move to {target}.",
            details={"status": current, "allowed": list(INVOICE_TRANSITIONS.get(current, ()))},
        )

    if target in {"APPROVED", "SENT"} and before["msa_required"]:
        raise BusinessRuleViolationError(
            "An active Master Service Agreement is required before this invoice can be approved.",
            details={"reason": "MSA_REQUIRED", "detail": before["msa_block_reason"]},
        )

    # Rule 7: a paid invoice is not casually editable.
    if current == "PAID" and target == "REFUNDED":
        pass

    await conn.execute(
        text(
            "UPDATE public.invoices SET status = CAST(:target AS invoice_status),"
            " disputed_reason = CASE WHEN :target = 'DISPUTED'"
            " THEN CAST(:reason AS text) ELSE disputed_reason END WHERE id = :rid"
        ),
        {"target": target, "reason": reason, "rid": before["id"]},
    )
    if target == "APPROVED":
        await conn.execute(
            text("UPDATE public.invoices SET locked = true WHERE id = :rid"), {"rid": before["id"]}
        )

    await audit.record(
        conn,
        action=f"invoice.{target.lower()}",
        resource_type="invoice",
        resource_id=before["id"],
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        old_values={"status": current},
        new_values={"status": target},
        reason=reason,
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_invoice(conn, company_id=company_id, public_id=public_id)


async def submit_for_approval(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    notes: str | None,
) -> dict[str, Any]:
    before = await resolve_scoped(conn, "invoices", public_id, company_id, lock=True)
    current = str(before["status"])
    if current not in {"DRAFT", "PENDING"}:
        raise InvalidStateTransitionError(
            f"An invoice in status {current} cannot be submitted for approval.",
            details={"status": current, "allowed": ["DRAFT", "PENDING"]},
        )
    if as_decimal(before["total_amount"]) <= 0:
        raise BusinessRuleViolationError(
            "An invoice with no value cannot be submitted.",
            details={"reason": "ZERO_VALUE_INVOICE"},
        )
    # Without an MSA the invoice stays in Draft: submission is refused here,
    # not merely at approval, so a No-MSA invoice can never enter the queue.
    if before["msa_required"]:
        raise BusinessRuleViolationError(
            "An active Master Service Agreement is required before this invoice can be submitted.",
            details={"reason": "MSA_REQUIRED", "detail": before["msa_block_reason"]},
        )

    await conn.execute(
        text("UPDATE public.invoices SET status = 'PENDING' WHERE id = :rid"), {"rid": before["id"]}
    )
    await conn.execute(
        text(
            """
            INSERT INTO public.invoice_approvals
              (invoice_id, step_no, name, approver_company_id, status)
            VALUES (:iid, 1, 'Invoice Approval', :cid, 'PENDING')
            ON CONFLICT (invoice_id, step_no) DO NOTHING
            """
        ),
        {"iid": before["id"], "cid": company_id},
    )
    await audit.record(
        conn,
        action="invoice.submitted_for_approval",
        resource_type="invoice",
        resource_public_id=public_id,
        resource_id=before["id"],
        company_id=company_id,
        actor_user_id=actor_user_id,
        old_values={"status": current},
        new_values={"status": "PENDING"},
        reason=notes,
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_invoice(conn, company_id=company_id, public_id=public_id)


async def decide_approval(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    step_no: int,
    decision: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    notes: str | None,
) -> dict[str, Any]:
    invoice = await resolve_scoped(conn, "invoices", public_id, company_id, lock=True)
    if str(invoice["status"]) not in {"PENDING", "SUBMITTED"}:
        raise InvalidStateTransitionError(
            f"An invoice in status {invoice['status']} is not awaiting approval.",
            details={"status": invoice["status"]},
        )

    updated = await conn.execute(
        text(
            """
            UPDATE public.invoice_approvals
               SET status = :decision, decided_at = now(), notes = :notes
             WHERE invoice_id = :iid AND step_no = :step AND status = 'PENDING'
            RETURNING step_no
            """
        ),
        {"decision": decision, "notes": notes, "iid": invoice["id"], "step": step_no},
    )
    if updated.mappings().first() is None:
        raise ResourceNotFoundError("That approval step is not pending.")

    if decision == "APPROVED":
        await conn.execute(
            text("UPDATE public.invoices SET status = 'SUBMITTED' WHERE id = :rid"),
            {"rid": invoice["id"]},
        )
        await conn.execute(
            text("UPDATE public.invoices SET status = 'APPROVED' WHERE id = :rid"),
            {"rid": invoice["id"]},
        )
    else:
        await conn.execute(
            text(
                "UPDATE public.invoices SET status = 'REJECTED', rejected_reason = :reason"
                " WHERE id = :rid"
            ),
            {"reason": notes, "rid": invoice["id"]},
        )

    await audit.record(
        conn,
        action=f"invoice.approval_{decision.lower()}",
        resource_type="invoice",
        resource_id=invoice["id"],
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        new_values={"step_no": step_no, "decision": decision},
        reason=notes,
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_invoice(conn, company_id=company_id, public_id=public_id)


async def cancel_invoice(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    reason: str,
) -> dict[str, Any]:
    before = await resolve_scoped(conn, "invoices", public_id, company_id, lock=True)
    if as_decimal(before["amount_paid"]) > 0:
        raise BusinessRuleViolationError(
            "This invoice already has payments against it. Issue a credit note instead.",
            details={"reason": "INVOICE_HAS_PAYMENTS"},
        )
    return await transition_invoice(
        conn,
        company_id=company_id,
        public_id=public_id,
        target="CANCELLED",
        actor_user_id=actor_user_id,
        request_id=request_id,
        ip_address=ip_address,
        reason=reason,
    )


async def issue_credit_note(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    amount: Decimal,
    reason: str,
) -> dict[str, Any]:
    """Credit note: a negative PAYABLE invoice against the same contract."""
    source = await resolve_scoped(conn, "invoices", public_id, company_id, lock=True)
    if amount <= 0:
        raise ValidationError("A credit note amount must be positive.")
    if amount > as_decimal(source["balance_due"]):
        raise BusinessRuleViolationError(
            "A credit note cannot exceed the invoice balance.",
            details={
                "reason": "CREDIT_EXCEEDS_BALANCE",
                "balance_due": str(as_decimal(source["balance_due"])),
                "requested": str(money(amount)),
            },
        )

    row = (
        (
            await conn.execute(
                text(
                    """
                    INSERT INTO public.invoices
                      (direction, company_id, counterparty_company_id, counterparty_user_id,
                       project_id, sow_id, contract_id, status, period_start, period_end,
                       issue_date, due_date, currency, payment_terms_days, notes,
                       terms_snapshot, created_by)
                    VALUES
                      ('PAYABLE', :cid, :counterparty_company, :counterparty_user,
                       :pid, :sid, :contract, 'DRAFT', :start, :end, :issue, :due,
                       :currency, 0, :notes, CAST(:snapshot AS jsonb), :actor)
                    RETURNING public_id
                    """
                ),
                {
                    "cid": company_id,
                    "counterparty_company": source["counterparty_company_id"],
                    "counterparty_user": source["counterparty_user_id"],
                    "pid": source["project_id"],
                    "sid": source["sow_id"],
                    "contract": source["contract_id"],
                    "start": source["period_start"],
                    "end": source["period_end"],
                    "issue": utc_today(),
                    "due": utc_today(),
                    "currency": source["currency"],
                    "notes": (
                        f"Credit note for "
                        f"{source['invoice_number'] or source['public_id']}: {reason}"
                    ),
                    "snapshot": _json(
                        {
                            "credit_note_for": source["invoice_number"] or source["public_id"],
                            "amount": str(money(amount)),
                            "reason": reason,
                        }
                    ),
                    "actor": actor_user_id,
                },
            )
        )
        .mappings()
        .first()
    )
    credit = await resolve_scoped(conn, "invoices", str(row["public_id"]), company_id)
    await conn.execute(
        text(
            """
            INSERT INTO public.invoice_items
              (invoice_id, contract_id, line_type, description, quantity, unit,
               unit_rate, currency)
            VALUES (:invoice, :contract, 'ADDITIONAL', :description, 1, 'LOT',
                    :amount, :currency)
            """
        ),
        {
            "invoice": credit["id"],
            "contract": credit["contract_id"],
            "description": f"Credit note against {source['invoice_number'] or source['public_id']}",
            "amount": -money(amount),
            "currency": credit["currency"],
        },
    )
    await conn.execute(
        text(
            """
            INSERT INTO public.invoice_allocations
              (invoice_id, source, amount, currency, allocated_by, note)
            VALUES (:iid, 'CREDIT_NOTE', :amount, :currency, :actor, :note)
            """
        ),
        {
            "iid": source["id"],
            "amount": -money(amount),
            "currency": source["currency"],
            "actor": actor_user_id,
            "note": reason,
        },
    )
    await conn.execute(
        text("UPDATE public.invoices SET status = 'APPROVED' WHERE id = :rid"),
        {"rid": credit["id"]},
    )

    await audit.record(
        conn,
        action="invoice.credit_note_issued",
        resource_type="invoice",
        resource_public_id=str(row["public_id"]),
        company_id=company_id,
        actor_user_id=actor_user_id,
        new_values={
            "against": source["invoice_number"] or source["public_id"],
            "amount": str(money(amount)),
        },
        reason=reason,
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_invoice(conn, company_id=company_id, public_id=str(row["public_id"]))


# =============================================================================
# dashboard aggregates
# =============================================================================
async def receivables_summary(conn: AsyncConnection, *, company_id: uuid.UUID) -> dict[str, Any]:
    """Aging buckets and totals, computed in SQL from the ledger."""
    row = (
        (
            await conn.execute(
                text(
                    """
                    SELECT
                      count(*) FILTER (
                        WHERE status IN ('APPROVED','PARTIALLY_PAID','OVERDUE')
                          AND balance_due > 0)                          AS open_count,
                      COALESCE(sum(balance_due) FILTER (
                        WHERE status IN ('APPROVED','PARTIALLY_PAID','OVERDUE')
                          AND balance_due > 0), 0)                       AS outstanding,
                      COALESCE(sum(balance_due) FILTER (
                        WHERE status IN ('APPROVED','PARTIALLY_PAID') AND balance_due > 0
                          AND due_date >= current_date), 0)             AS not_yet_due,
                      COALESCE(sum(balance_due) FILTER (
                        WHERE status IN ('APPROVED','PARTIALLY_PAID','OVERDUE')
                          AND balance_due > 0 AND due_date < current_date), 0) AS overdue,
                      COALESCE(sum(total_amount) FILTER (
                        WHERE status IN ('APPROVED','PARTIALLY_PAID','OVERDUE')
                          AND period_end >= date_trunc('month', current_date)::date), 0)
                                                                          AS invoiced_this_month,
                      COALESCE(sum(amount_paid) FILTER (
                        WHERE paid_at >= date_trunc('month', current_date)::date), 0)
                                                                          AS collected_this_month
                      FROM public.invoices
                     WHERE company_id = :cid AND direction = 'RECEIVABLE' AND deleted_at IS NULL
                    """
                ),
                {"cid": company_id},
            )
        )
        .mappings()
        .first()
    )
    totals: dict[str, Any] = {k: money(v) for k, v in dict(row or {}).items()}
    data: dict[str, Any] = dict(totals)

    buckets = await conn.execute(
        text(
            """
            SELECT
              CASE
                WHEN due_date < current_date THEN 'OVERDUE'
                WHEN due_date <= current_date + 30 THEN 'DUE_1_30'
                WHEN due_date <= current_date + 60 THEN 'DUE_31_60'
                WHEN due_date <= current_date + 90 THEN 'DUE_61_90'
                ELSE 'DUE_90_PLUS'
              END AS bucket,
              count(*) AS invoice_count,
              COALESCE(sum(balance_due), 0) AS amount
              FROM public.invoices
             WHERE company_id = :cid AND direction = 'RECEIVABLE'
               AND deleted_at IS NULL
               AND status IN ('APPROVED','PARTIALLY_PAID','OVERDUE')
               AND balance_due > 0
             GROUP BY 1 ORDER BY 1
            """
        ),
        {"cid": company_id},
    )
    data["aging"] = [
        {
            "bucket": b["bucket"],
            "invoice_count": int(b["invoice_count"]),
            "amount": money(b["amount"]),
        }
        for b in buckets.mappings().all()
    ]

    late = await conn.execute(
        text(
            """
            SELECT COALESCE(cp.display_name, cp.legal_name) AS counterparty,
                   count(*) AS invoice_count,
                   COALESCE(sum(i.balance_due), 0) AS amount,
                   max(current_date - i.due_date) AS days_late
              FROM public.invoices i
              JOIN public.companies cp ON cp.id = i.counterparty_company_id
             WHERE i.company_id = :cid AND i.direction = 'RECEIVABLE'
               AND i.deleted_at IS NULL
               AND i.balance_due > 0 AND i.due_date < current_date
             GROUP BY COALESCE(cp.display_name, cp.legal_name)
             ORDER BY amount DESC LIMIT 10
            """
        ),
        {"cid": company_id},
    )
    data["late_payers"] = [
        {
            "counterparty": r["counterparty"],
            "invoice_count": int(r["invoice_count"]),
            "amount": money(r["amount"]),
            "days_late": int(r["days_late"] or 0),
        }
        for r in late.mappings().all()
    ]

    revenue = await conn.execute(
        text(
            """
            SELECT pr.public_id AS role_id, pr.title AS role_title,
                   COALESCE(sum(i.total_amount), 0) AS invoiced
              FROM public.invoice_items it
              JOIN public.invoices i   ON i.id = it.invoice_id
              JOIN public.contract_roles cr ON cr.id = it.contract_role_id
              JOIN public.project_roles pr  ON pr.id = cr.project_role_id
             WHERE i.company_id = :cid AND i.deleted_at IS NULL
               AND i.status NOT IN ('DRAFT','PENDING','CANCELLED','REJECTED')
             GROUP BY pr.public_id, pr.title
             ORDER BY invoiced DESC LIMIT 20
            """
        ),
        {"cid": company_id},
    )
    data["revenue_by_role"] = [
        {"role_id": r["role_id"], "role_title": r["role_title"], "invoiced": money(r["invoiced"])}
        for r in revenue.mappings().all()
    ]

    by_project = await conn.execute(
        text(
            """
            SELECT p.public_id AS project_id, p.name AS project_name,
                   COALESCE(sum(i.total_amount), 0) AS invoiced,
                   COALESCE(sum(i.balance_due), 0)  AS outstanding
              FROM public.invoices i
              JOIN public.projects p ON p.id = i.project_id
             WHERE i.company_id = :cid AND i.deleted_at IS NULL
               AND i.status NOT IN ('DRAFT','PENDING','CANCELLED','REJECTED')
             GROUP BY p.public_id, p.name
             ORDER BY invoiced DESC LIMIT 20
            """
        ),
        {"cid": company_id},
    )
    data["revenue_by_project"] = [
        {
            "project_id": r["project_id"],
            "project_name": r["project_name"],
            "invoiced": money(r["invoiced"]),
            "outstanding": money(r["outstanding"]),
        }
        for r in by_project.mappings().all()
    ]

    return data
