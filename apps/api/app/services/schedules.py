"""Recurring payment schedules: CRUD plus on-demand advancement.

Creating a SCHEDULED payment never moves money: each occurrence still needs
its own authorization before initiation. The Celery beat task
`advance_payment_schedules` does the same work on a timer; `run_due` exposes
it on demand with the same one-occurrence-per-day idempotency.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.clock import utc_today
from app.core.errors import BusinessRuleViolationError, ResourceNotFoundError, ValidationError
from app.services import audit

_FREQUENCIES = {"WEEKLY", "BIWEEKLY", "MONTHLY", "QUARTERLY"}
_STATUSES = {"ACTIVE", "PAUSED", "COMPLETED", "CANCELLED"}

_SCHEDULE_SELECT = """
    SELECT s.id, s.public_id, s.company_id, s.contract_id, s.invoice_id,
           s.payment_account_id, s.frequency, s.amount, s.currency,
           s.payment_method, s.next_run_date, s.end_date, s.occurrences,
           s.run_count, s.auto_submit, s.status, s.created_by,
           s.created_at, s.updated_at
      FROM public.payment_schedules s
"""


def _schedule_from_row(row: Any) -> dict[str, Any]:
    data = dict(row)
    for key in ("id", "company_id", "contract_id", "invoice_id", "payment_account_id"):
        if data.get(key) is not None:
            data[key] = str(data[key])
    return data


async def create_schedule(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    from app.services.billing import money

    amount = money(payload.get("amount", 0))
    if amount <= 0:
        raise ValidationError("A schedule amount must be greater than zero.")
    frequency = str(payload.get("frequency") or "").upper()
    if frequency not in _FREQUENCIES:
        raise ValidationError(f"Unknown frequency: {frequency}.")
    try:
        next_run = date.fromisoformat(str(payload.get("next_run_date") or ""))
    except ValueError:
        raise ValidationError("next_run_date must be YYYY-MM-DD.") from None
    end_date = payload.get("end_date")
    if end_date:
        try:
            end_date = date.fromisoformat(str(end_date))
        except ValueError:
            raise ValidationError("end_date must be YYYY-MM-DD.") from None
        if end_date < next_run:
            raise ValidationError("end_date cannot precede next_run_date.")

    row = (
        (
            await conn.execute(
                text(
                    """
                    INSERT INTO public.payment_schedules
                      (company_id, contract_id, invoice_id, payment_account_id,
                       frequency, amount, currency, payment_method, next_run_date,
                       end_date, occurrences, auto_submit, status, created_by)
                    VALUES (:cid, :contract, :invoice, :account,
                            :frequency, :amount, :currency, :method, :next_run,
                            :end_date, :occurrences, :auto_submit, 'ACTIVE', :actor)
                    RETURNING public_id
                    """
                ),
                {
                    "cid": company_id,
                    "contract": payload.get("contract_id"),
                    "invoice": payload.get("invoice_id"),
                    "account": payload.get("payment_account_id"),
                    "frequency": frequency,
                    "amount": amount,
                    "currency": payload.get("currency", "USD"),
                    "method": payload.get("payment_method", "ACH"),
                    "next_run": next_run,
                    "end_date": end_date,
                    "occurrences": payload.get("occurrences"),
                    "auto_submit": bool(payload.get("auto_submit", False)),
                    "actor": actor_user_id,
                },
            )
        )
        .mappings()
        .first()
    )
    created = await get_schedule(conn, company_id=company_id, public_id=str(row["public_id"]))
    await audit.record(
        conn,
        action="payment.schedule_created",
        resource_type="payment_schedule",
        resource_public_id=str(row["public_id"]),
        company_id=company_id,
        actor_user_id=actor_user_id,
        request_id=request_id,
        ip_address=ip_address,
    )
    return created


async def get_schedule(
    conn: AsyncConnection, *, company_id: uuid.UUID, public_id: str
) -> dict[str, Any]:
    row = (
        (
            await conn.execute(
                text(f"{_SCHEDULE_SELECT} WHERE s.public_id = :pid AND s.company_id = :cid"),
                {"pid": public_id, "cid": company_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ResourceNotFoundError("Payment schedule not found.")
    return _schedule_from_row(row)


async def list_schedules(
    conn: AsyncConnection, *, company_id: uuid.UUID, limit: int
) -> list[dict[str, Any]]:
    rows = (
        (
            await conn.execute(
                text(
                    f"""{_SCHEDULE_SELECT}
                     WHERE s.company_id = :cid AND s.deleted_at IS NULL
                     ORDER BY s.next_run_date ASC
                     LIMIT :limit"""
                ),
                {"cid": company_id, "limit": limit},
            )
        )
        .mappings()
        .all()
    )
    return [_schedule_from_row(r) for r in rows]


async def set_schedule_status(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    status: str,
) -> dict[str, Any]:
    status = str(status or "").upper()
    if status not in {"PAUSED", "CANCELLED"}:
        raise ValidationError(f"Schedules can only be paused or cancelled, not {status}.")
    schedule = await get_schedule(conn, company_id=company_id, public_id=public_id)
    if schedule["status"] not in {"ACTIVE", "PAUSED"}:
        raise BusinessRuleViolationError(
            f"A schedule in status {schedule['status']} cannot change.",
            details={"reason": "SCHEDULE_CLOSED", "status": schedule["status"]},
        )
    await conn.execute(
        text("UPDATE public.payment_schedules SET status = :status WHERE public_id = :pid"),
        {"status": status, "pid": public_id},
    )
    await audit.record(
        conn,
        action=f"payment.schedule_{status.lower()}",
        resource_type="payment_schedule",
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        request_id=request_id,
    )
    return await get_schedule(conn, company_id=company_id, public_id=public_id)


async def run_due_schedules(
    conn: AsyncConnection, *, company_id: uuid.UUID, actor_user_id: uuid.UUID, request_id: str
) -> dict[str, Any]:
    """Create today's occurrences for this company's due schedules.

    Same statement the beat task runs, scoped to one company. Explicit
    authorization is still required per occurrence before money moves: these
    rows land SCHEDULED, never INITIATED.
    """
    from app.services.billing import money

    due = (
        (
            await conn.execute(
                text(
                    """
                    SELECT id::text, public_id, amount, currency, payment_method,
                           next_run_date
                      FROM public.payment_schedules
                     WHERE company_id = :cid AND status = 'ACTIVE'
                       AND deleted_at IS NULL
                       AND next_run_date <= current_date
                     LIMIT 200
                    """
                ),
                {"cid": company_id},
            )
        )
        .mappings()
        .all()
    )
    created = 0
    for row in due:
        result = await conn.execute(
            text(
                """
                INSERT INTO public.payments
                  (public_id, company_id, direction, status, amount, currency,
                   payment_method, scheduled_for, authorization_type, processor,
                   metadata, idempotency_key, created_by)
                VALUES
                  ('PM' || upper(substr(md5(random()::text), 1, 6)), CAST(:cid AS uuid),
                   'PAYABLE', 'SCHEDULED', :amount, :ccy, :method, current_date,
                   'SCHEDULED', 'MANUAL',
                   jsonb_build_object(
                     'schedule_public_id', CAST(:pid AS text), 'source', 'recurring'),
                   :idem, :actor)
                ON CONFLICT (idempotency_key) DO NOTHING
                """
            ),
            {
                "cid": company_id,
                "amount": money(row["amount"] or 0),
                "ccy": row["currency"],
                "method": row["payment_method"],
                "pid": row["public_id"],
                "idem": f"schedule:{row['public_id']}:{row['next_run_date']}",
                "actor": actor_user_id,
            },
        )
        if result.rowcount:
            created += 1
        await conn.execute(
            text("SELECT app.advance_schedule(CAST(:id AS uuid), current_date)"),
            {"id": row["id"]},
        )
    await audit.record(
        conn,
        action="payment.schedules_run",
        resource_type="company",
        resource_id=company_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        new_values={"created": created, "run_date": str(utc_today())},
        request_id=request_id,
    )
    return {"created": created}
