"""WORK module: assignments, timesheets, timesheet entries, approvals and leave.

Invariants enforced by the database that this module relies on rather than
re-implements:

  * `app.can_record_time` — a timesheet needs an ACTIVE assignment on an
    ACCEPTED/ACTIVE contract covering the period (rule 1, indirectly).
  * `app.compute_entry` — derives hours from clock times, snapshots the rate
    from the assignment and computes the amount (rules 1, 4, 10).
  * `app.refresh_timesheet_totals` — hours/amount are always derived from entries.
  * `app.assert_timesheet_transition` — the status machine and self-approval rule.
  * `app.build_approval_chain` (trigger) — materialises approval steps when the
    sheet reaches SUBMITTED, then flips it to UNDER_REVIEW.
  * `app.advance_timesheet_on_final_approval` (trigger) — approves or rejects the
    sheet once the last step is decided.
  * `app.assert_timesheet_editable` (0015) — approved/locked sheets take no entry
    changes outside the trusted worker context, and every change leaves a
    `timesheet_revisions` row (rules 3 and 6).
  * `app.assert_leave_request` / `app.apply_leave_decision` — policy, notice,
    balance and segregation-of-duties rules for leave.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal
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
from app.services.code import _jsonable
from app.services.lookup import as_decimal, resolve_scoped, resolve_user_public_id

logger = get_logger(__name__)

ZERO = Decimal("0")


def _billing_frequency_for(start: date, end: date, requested: str | None) -> str:
    if requested:
        return requested
    span = (end - start).days + 1
    if span <= 7:
        return "WEEKLY"
    if span <= 14:
        return "BIWEEKLY"
    if span <= 92:
        return "MONTHLY"
    return "QUARTERLY"


# =============================================================================
# assignments
# =============================================================================
_ASSIGNMENT_SELECT = """
    SELECT a.id, a.contract_id, a.contract_role_id, a.project_id, a.company_id, a.user_id,
           a.role_title, a.hourly_rate, a.currency, a.start_date, a.end_date, a.status,
           a.allocation_pct, a.source, a.created_at, a.updated_at,
           u.public_id AS user_public_id,
           NULLIF(TRIM(u.first_name || ' ' || u.last_name), '') AS user_name,
           c.public_id AS contract_public_id, c.title AS contract_title,
           c.status AS contract_status,
           p.public_id AS project_public_id, p.name AS project_name,
           pr.public_id AS project_role_public_id, pr.title AS project_role_title,
           COALESCE(h.hours_to_date, 0) AS hours_to_date
      FROM public.assignments a
      JOIN public.users u ON u.id = a.user_id
      JOIN public.contracts c ON c.id = a.contract_id
      JOIN public.projects p ON p.id = a.project_id
      LEFT JOIN public.contract_roles cr ON cr.id = a.contract_role_id
      LEFT JOIN public.project_roles pr ON pr.id = cr.project_role_id
      LEFT JOIN LATERAL (
            SELECT sum(t.billable_hours) AS hours_to_date
              FROM public.timesheets t
             WHERE t.assignment_id = a.id AND t.status IN ('APPROVED','LOCKED')
      ) h ON TRUE
"""


def _assignment_from_row(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["user_id"] = data.pop("user_public_id")
    data["contract_id"] = data.pop("contract_public_id")
    data["project_id"] = data.pop("project_public_id")
    data["role_id"] = data.pop("project_role_public_id", None)
    data.pop("project_role_title", None)
    data.pop("project_name", None)
    data.pop("contract_title", None)
    return data


async def list_assignments(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    project_public_id: str | None = None,
    contract_public_id: str | None = None,
    user_public_id: str | None = None,
    mine: bool = False,
    actor_user_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: int,
    cursor_keys: dict[str, str],
) -> list[dict[str, Any]]:
    where = ["a.company_id = :cid"]
    params: dict[str, Any] = {"cid": company_id, "limit": limit + 1}

    if project_public_id:
        project = await resolve_scoped(conn, "projects", project_public_id, company_id)
        where.append("a.project_id = :pid")
        params["pid"] = project["id"]
    if contract_public_id:
        contract = await resolve_scoped(conn, "contracts", contract_public_id, company_id)
        where.append("a.contract_id = :contract")
        params["contract"] = contract["id"]
    if mine and actor_user_id is not None:
        where.append("a.user_id = :uid")
        params["uid"] = actor_user_id
    elif user_public_id:
        resolved = await resolve_user_public_id(conn, user_public_id, company_id=company_id)
        where.append("a.user_id = :uid")
        params["uid"] = resolved
    if status:
        where.append("a.status = :status")
        params["status"] = status
    if cursor_keys.get("created_at"):
        where.append("(a.created_at, a.user_id) < (:cur_created, :cur_user)")
        params["cur_created"] = cursor_keys["created_at"]
        params["cur_user"] = cursor_keys.get("user_id") or cursor_keys.get("public_id", "")

    rows = (
        (
            await conn.execute(
                text(
                    f"""
                    {_ASSIGNMENT_SELECT}
                     WHERE {" AND ".join(where)}
                     ORDER BY a.created_at DESC, a.user_id DESC
                     LIMIT :limit
                    """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    return [_assignment_from_row(r) for r in rows]


async def create_assignment(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    contract = await resolve_scoped(conn, "contracts", payload["contract_id"], company_id)
    user_id = await resolve_user_public_id(conn, payload["user_id"], company_id=company_id)

    contract_role_id = None
    rate = None
    role_title = payload.get("role_title") or "Contractor"
    if payload.get("project_role_id"):
        role = await resolve_scoped(conn, "project_roles", payload["project_role_id"], company_id)
        row = (
            (
                await conn.execute(
                    text(
                        """
                        SELECT id::text, rate
                          FROM public.contract_roles
                         WHERE contract_id = :cid AND project_role_id = :prid
                        """
                    ),
                    {"cid": contract["id"], "prid": role["id"]},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise BusinessRuleViolationError(
                "That role is not covered by this contract. Add it to the contract roles first.",
                details={
                    "reason": "ROLE_NOT_ON_CONTRACT",
                    "contract_id": payload["contract_id"],
                    "project_role_id": payload["project_role_id"],
                },
            )
        contract_role_id = row["id"]
        rate = as_decimal(row["rate"], "0")
        role_title = role["title"]

    if payload.get("hourly_rate") is not None:
        rate = as_decimal(payload["hourly_rate"])

    existing = await conn.execute(
        text(
            """
            SELECT id::text, status FROM public.assignments
             WHERE contract_id = :cid AND user_id = :uid
               AND contract_role_id IS NOT DISTINCT FROM :crid
               AND status IN ('PENDING','ACTIVE','ON_LEAVE')
            """
        ),
        {"cid": contract["id"], "uid": user_id, "crid": contract_role_id},
    )
    if existing.mappings().first() is not None:
        raise BusinessRuleViolationError(
            "That person already has a live assignment on this contract for this role.",
            details={"reason": "DUPLICATE_ASSIGNMENT"},
        )

    row = (
        (
            await conn.execute(
                text(
                    """
                    INSERT INTO public.assignments
                      (contract_id, contract_role_id, project_id, company_id, user_id,
                       role_title, hourly_rate, currency, start_date, end_date,
                       allocation_pct, source, status, created_at)
                    VALUES
                      (:cid, CAST(:crid AS uuid), :pid, :company, :uid,
                       :role_title, :rate, :currency, :start_date, :end_date,
                       :allocation, 'CONTRACT',
                       CASE WHEN (SELECT status FROM public.contracts WHERE id = :cid) = 'ACTIVE'
                            THEN 'ACTIVE' ELSE 'PENDING' END,
                       now())
                    RETURNING id::text
                    """
                ),
                {
                    "cid": contract["id"],
                    "crid": contract_role_id,
                    "pid": contract["project_id"],
                    "company": company_id,
                    "uid": user_id,
                    "role_title": role_title,
                    "rate": rate,
                    "currency": payload.get("currency") or contract["currency"],
                    "start_date": payload.get("start_date")
                    or contract["start_date"]
                    or utc_today(),
                    "end_date": payload.get("end_date") or contract["end_date"],
                    "allocation": payload.get("allocation_pct", 100),
                },
            )
        )
        .mappings()
        .first()
    )

    assignment_id = uuid.UUID(str(row["id"]))
    if contract_role_id:
        await conn.execute(
            text(
                "SELECT app.refresh_role_allocation(cr.project_role_id)"
                " FROM public.contract_roles cr"
                " WHERE cr.id = CAST(:crid AS uuid)"
            ),
            {"crid": contract_role_id},
        )

    await audit.record(
        conn,
        action="assignment.created",
        resource_type="assignment",
        resource_id=assignment_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        new_values={
            "contract_id": payload["contract_id"],
            "project_role_id": payload.get("project_role_id"),
            "user_id": payload["user_id"],
            "start_date": payload.get("start_date"),
            "allocation_pct": payload.get("allocation_pct", 100),
        },
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_assignment(conn, company_id=company_id, assignment_id=assignment_id)


async def get_assignment(
    conn: AsyncConnection, *, company_id: uuid.UUID, assignment_id: uuid.UUID
) -> dict[str, Any]:
    row = (
        (
            await conn.execute(
                text(f"{_ASSIGNMENT_SELECT} WHERE a.id = :rid AND a.company_id = :cid"),
                {"rid": assignment_id, "cid": company_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ResourceNotFoundError("Assignment not found.")
    return _assignment_from_row(row)


async def update_assignment(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    assignment_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    changes: dict[str, Any],
) -> dict[str, Any]:
    row = (
        (
            await conn.execute(
                text(
                    """
                    SELECT * FROM public.assignments
                     WHERE id = :rid AND company_id = :cid FOR UPDATE
                    """
                ),
                {"rid": assignment_id, "cid": company_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ResourceNotFoundError("Assignment not found.")

    updates = {
        k: v
        for k, v in changes.items()
        if k in {"start_date", "end_date", "allocation_pct", "role_title", "hourly_rate", "status"}
        and v is not None
    }
    if (
        updates.get("start_date")
        and updates.get("end_date")
        and updates["end_date"] < updates["start_date"]
    ):
        raise ValidationError("The assignment end date must not precede the start date.")

    if updates:
        assignments = ", ".join(f"{col} = :{col}" for col in updates)
        await conn.execute(
            text(f"UPDATE public.assignments SET {assignments} WHERE id = :rid"),  # noqa: S608
            {**updates, "rid": assignment_id},
        )

    await audit.record(
        conn,
        action="assignment.updated",
        resource_type="assignment",
        resource_id=assignment_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        old_values={k: _jsonable(row.get(k)) for k in updates},
        new_values={k: _jsonable(v) for k, v in updates.items()},
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_assignment(conn, company_id=company_id, assignment_id=assignment_id)


async def end_assignment(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    assignment_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    end_date: date,
    reason: str,
) -> dict[str, Any]:
    row = (
        (
            await conn.execute(
                text(
                    """
                    SELECT * FROM public.assignments
                     WHERE id = :rid AND company_id = :cid FOR UPDATE
                    """
                ),
                {"rid": assignment_id, "cid": company_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ResourceNotFoundError("Assignment not found.")

    open_sheets = await conn.execute(
        text(
            """
            SELECT count(*) FROM public.timesheets
             WHERE assignment_id = :rid AND status IN ('SUBMITTED','UNDER_REVIEW')
            """
        ),
        {"rid": assignment_id},
    )
    if as_decimal(open_sheets.scalar()) > 0:
        raise BusinessRuleViolationError(
            "Resolve the timesheets awaiting approval before ending this assignment.",
            details={"open_timesheets": int(as_decimal(open_sheets.scalar()))},
        )

    await conn.execute(
        text(
            """
            UPDATE public.assignments
               SET status = 'COMPLETED', end_date = :end
             WHERE id = :rid
            """
        ),
        {"end": end_date, "rid": assignment_id},
    )
    await audit.record(
        conn,
        action="assignment.ended",
        resource_type="assignment",
        resource_id=assignment_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        old_values={"status": row["status"], "end_date": row["end_date"]},
        new_values={"status": "COMPLETED", "end_date": str(end_date)},
        reason=reason,
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_assignment(conn, company_id=company_id, assignment_id=assignment_id)


# =============================================================================
# timesheets
# =============================================================================
_TIMESHEET_SELECT = """
    SELECT t.id, t.public_id, t.user_id, t.company_id, t.assignment_id, t.contract_id,
           t.contract_role_id, t.project_id, t.period_start, t.period_end,
           t.billing_frequency, t.status, t.total_hours, t.billable_hours, t.total_amount,
           t.currency, t.entry_count, t.current_step, t.locked_at, t.submitted_at,
           t.approved_at, t.rejection_reason, t.created_at, t.updated_at,
           u.public_id AS user_public_id,
           NULLIF(TRIM(u.first_name || ' ' || u.last_name), '') AS user_name,
           c.public_id AS contract_public_id, c.title AS contract_title,
           p.public_id AS project_public_id, p.name AS project_name,
           pr.public_id AS project_role_public_id, pr.title AS project_role_title,
           a.role_title AS assignment_role_title
      FROM public.timesheets t
      JOIN public.users u ON u.id = t.user_id
      JOIN public.contracts c ON c.id = t.contract_id
      JOIN public.projects p ON p.id = t.project_id
      LEFT JOIN public.contract_roles cr ON cr.id = t.contract_role_id
      LEFT JOIN public.project_roles pr ON pr.id = cr.project_role_id
      JOIN public.assignments a ON a.id = t.assignment_id
"""


def _timesheet_from_row(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["user_id"] = data.pop("user_public_id")
    data["contract_id"] = data.pop("contract_public_id")
    data["project_id"] = data.pop("project_public_id")
    data["role_id"] = data.pop("project_role_public_id", None)
    data["role_title"] = data.pop("project_role_title", None) or data.get("assignment_role_title")
    data.pop("project_name", None)
    data.pop("contract_title", None)
    data.pop("assignment_role_title", None)
    return data


async def list_timesheets(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    mine: bool = False,
    project_public_id: str | None = None,
    contract_public_id: str | None = None,
    user_public_id: str | None = None,
    status: str | None = None,
    pending_approval: bool = False,
    limit: int,
    cursor_keys: dict[str, str],
) -> list[dict[str, Any]]:
    where = ["t.company_id = :cid"]
    params: dict[str, Any] = {"cid": company_id, "limit": limit + 1}

    if mine:
        where.append("t.user_id = :uid")
        params["uid"] = actor_user_id
    elif user_public_id:
        resolved = await resolve_user_public_id(conn, user_public_id, company_id=company_id)
        where.append("t.user_id = :uid")
        params["uid"] = resolved
    if project_public_id:
        project = await resolve_scoped(conn, "projects", project_public_id, company_id)
        where.append("t.project_id = :pid")
        params["pid"] = project["id"]
    if contract_public_id:
        contract = await resolve_scoped(conn, "contracts", contract_public_id, company_id)
        where.append("t.contract_id = :contract")
        params["contract"] = contract["id"]
    if status:
        where.append("t.status = :status")
        params["status"] = status
    if pending_approval:
        where.append("t.status IN ('SUBMITTED','UNDER_REVIEW')")
    if cursor_keys.get("period_start"):
        where.append("(t.period_start, t.public_id) < (:cur_period, :cur_public)")
        params["cur_period"] = cursor_keys["period_start"]
        params["cur_public"] = cursor_keys["public_id"]

    rows = (
        (
            await conn.execute(
                text(
                    f"""
                    {_TIMESHEET_SELECT}
                     WHERE {" AND ".join(where)}
                     ORDER BY t.period_start DESC, t.public_id DESC
                     LIMIT :limit
                    """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    return [_timesheet_from_row(r) for r in rows]


async def get_timesheet(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    actor_user_id: uuid.UUID,
    with_entries: bool = True,
) -> dict[str, Any]:
    row = (
        (
            await conn.execute(
                text(f"{_TIMESHEET_SELECT} WHERE t.public_id = :pid AND t.company_id = :cid"),
                {"pid": public_id, "cid": company_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ResourceNotFoundError("Timesheet not found.")

    data = _timesheet_from_row(row)
    data["editable"] = str(row["status"]) in {"DRAFT", "REJECTED"} and str(row["user_id"]) == str(
        actor_user_id
    )

    if with_entries:
        data["entries"] = await list_entries(conn, timesheet_id=row["id"])
    data["approvals"] = (
        (
            await conn.execute(
                text(
                    """
                    SELECT step_no, status, required_permission, notes, requested_at, decided_at
                      FROM public.timesheet_approvals
                     WHERE timesheet_id = :tid
                     ORDER BY step_no
                    """
                ),
                {"tid": row["id"]},
            )
        )
        .mappings()
        .all()
    )
    data["approvals"] = [dict(a) for a in data["approvals"]]
    data["revisions"] = (
        (
            await conn.execute(
                text(
                    """
                    SELECT revision_no, change_type, summary, reason, created_at
                      FROM public.timesheet_revisions
                     WHERE timesheet_id = :tid
                     ORDER BY revision_no DESC
                     LIMIT 50
                    """
                ),
                {"tid": row["id"]},
            )
        )
        .mappings()
        .all()
    )
    data["revisions"] = [dict(r) for r in data["revisions"]]
    return data


async def create_timesheet(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Open a weekly (or otherwise-periodised) timesheet against an assignment.

    The role is never free text: `role_id` must be the `R...` identifier on the
    assignment's contract role, so an entry can never be filed against a role the
    contract does not cover.
    """
    assignment_id = payload.get("assignment_id")
    if not assignment_id:
        raise ValidationError(
            "Select an assignment before opening a timesheet.",
            details={"field": "assignment_id"},
        )

    row = (
        (
            await conn.execute(
                text(
                    """
                    SELECT a.id, a.contract_id, a.project_id, a.contract_role_id, a.user_id,
                           a.start_date, a.end_date, a.status AS assignment_status,
                           c.status AS contract_status, c.currency, c.project_id AS c_project,
                           cr.project_role_id, pr.public_id AS role_public_id
                      FROM public.assignments a
                      JOIN public.contracts c ON c.id = a.contract_id
                      LEFT JOIN public.contract_roles cr ON cr.id = a.contract_role_id
                      LEFT JOIN public.project_roles pr ON pr.id = cr.project_role_id
                     WHERE a.id = :rid AND a.company_id = :cid
                    """
                ),
                {"rid": assignment_id, "cid": company_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ResourceNotFoundError("Assignment not found.")

    if payload.get("user_id") and payload["user_id"] != str(row["user_id"]):
        target = await resolve_user_public_id(conn, payload["user_id"], company_id=company_id)
        if str(target) != str(row["user_id"]):
            raise BusinessRuleViolationError(
                "That assignment belongs to somebody else.",
                details={"reason": "ASSIGNMENT_OWNER_MISMATCH"},
            )

    if (
        payload.get("role_id")
        and row["role_public_id"]
        and payload["role_id"] != row["role_public_id"]
    ):
        raise BusinessRuleViolationError(
            "The selected role is not the one this assignment covers.",
            details={"reason": "TIMESHEET_ROLE_MISMATCH", "expected": row["role_public_id"]},
        )

    period_start = payload.get("period_start") or row["start_date"] or utc_today()
    period_end = payload.get("period_end")
    if period_end is None:
        period_end = period_start + timedelta(days=6)
    if period_end < period_start:
        raise ValidationError("The period end must not precede the period start.")

    inserted = (
        (
            await conn.execute(
                text(
                    """
                    INSERT INTO public.timesheets
                      (user_id, company_id, assignment_id, contract_id, contract_role_id,
                       project_id, period_start, period_end, billing_frequency, status,
                       currency)
                    VALUES
                      (:uid, :cid, :assignment, :contract, CAST(:crid AS uuid),
                       :project, :period_start, :period_end, :frequency, 'DRAFT',
                       :currency)
                    RETURNING public_id
                    """
                ),
                {
                    "uid": row["user_id"],
                    "cid": company_id,
                    "assignment": assignment_id,
                    "contract": row["contract_id"],
                    "crid": row["contract_role_id"],
                    "project": row["project_id"],
                    "period_start": period_start,
                    "period_end": period_end,
                    "frequency": _billing_frequency_for(
                        period_start, period_end, payload.get("billing_frequency")
                    ),
                    "currency": row["currency"],
                },
            )
        )
        .mappings()
        .first()
    )

    await audit.record(
        conn,
        action="timesheet.created",
        resource_type="timesheet",
        resource_public_id=str(inserted["public_id"]),
        company_id=company_id,
        actor_user_id=actor_user_id,
        new_values={
            "assignment_id": str(assignment_id),
            "period_start": str(period_start),
            "period_end": str(period_end),
        },
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_timesheet(
        conn,
        company_id=company_id,
        public_id=str(inserted["public_id"]),
        actor_user_id=actor_user_id,
    )


async def list_entries(conn: AsyncConnection, *, timesheet_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        (
            await conn.execute(
                text(
                    """
                    SELECT e.id::text, e.entry_date, e.start_time, e.end_time,
                           e.break_minutes, e.hours, e.is_billable, e.work_description,
                           e.project_task, e.rate_applied, e.amount, e.currency, e.source
                      FROM public.timesheet_entries e
                     WHERE e.timesheet_id = :tid
                     ORDER BY e.entry_date, e.start_time NULLS FIRST, e.id
                    """
                ),
                {"tid": timesheet_id},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def _timesheet_row(
    conn: AsyncConnection, *, company_id: uuid.UUID, public_id: str, lock: bool = False
) -> dict[str, Any]:
    base = "SELECT * FROM public.timesheets WHERE public_id = :pid AND company_id = :cid"
    sql = base + (" FOR UPDATE" if lock else "")
    row = (await conn.execute(text(sql), {"pid": public_id, "cid": company_id})).mappings().first()
    if row is None:
        raise ResourceNotFoundError("Timesheet not found.")
    return dict(row)


def _assert_editable(sheet: dict[str, Any], actor_user_id: uuid.UUID) -> None:
    status = str(sheet["status"])
    if status not in {"DRAFT", "REJECTED"}:
        raise InvalidStateTransitionError(
            f"A timesheet in status {status} cannot be edited.",
            details={"status": status, "editable_in": ["DRAFT", "REJECTED"]},
        )
    if str(sheet["user_id"]) != str(actor_user_id):
        raise BusinessRuleViolationError(
            "Only the owner of a timesheet may edit its entries.",
            details={"reason": "TIMESHEET_NOT_OWNED"},
        )


async def add_entry(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    sheet = await _timesheet_row(conn, company_id=company_id, public_id=public_id, lock=True)
    _assert_editable(sheet, actor_user_id)

    entry_date = payload["entry_date"]
    if not (sheet["period_start"] <= entry_date <= sheet["period_end"]):
        raise ValidationError(
            "That date falls outside this timesheet's period.",
            details={
                "reason": "ENTRY_OUTSIDE_PERIOD",
                "period_start": str(sheet["period_start"]),
                "period_end": str(sheet["period_end"]),
                "entry_date": str(entry_date),
            },
        )

    # Approved leave must never be re-entered as billable time.
    clash = await conn.execute(
        text(
            """
            SELECT r.public_id, r.status, r.total_days
              FROM public.leave_requests r
             WHERE r.user_id = :uid AND r.company_id = :cid
               AND r.start_date <= :entry_date AND r.end_date >= :entry_date
               AND r.status = 'APPROVED'
            """
        ),
        {"uid": sheet["user_id"], "cid": company_id, "entry_date": entry_date},
    )
    conflicting = clash.mappings().first()
    if conflicting:
        raise BusinessRuleViolationError(
            "This day is covered by approved leave and cannot be recorded as billable time.",
            details={
                "reason": "APPROVED_LEAVE_CONFLICT",
                "leave_request_id": conflicting["public_id"],
                "entry_date": str(entry_date),
            },
        )

    row = (
        (
            await conn.execute(
                text(
                    """
                    INSERT INTO public.timesheet_entries
                      (timesheet_id, entry_date, start_time, end_time, break_minutes,
                       hours, is_billable, work_description, project_task, source)
                    VALUES
                      (:tid, :entry_date, :start_time, :end_time, :break,
                       :hours, :is_billable, :description, :task, :source)
                    RETURNING id::text
                    """
                ),
                {
                    "tid": sheet["id"],
                    "entry_date": entry_date,
                    "start_time": payload.get("start_time"),
                    "end_time": payload.get("end_time"),
                    "break": payload.get("break_minutes", 0),
                    "hours": payload.get("hours", 0),
                    "is_billable": payload.get("is_billable", True),
                    "description": payload.get("work_description", ""),
                    "task": payload.get("project_task"),
                    "source": payload.get("source", "MANUAL"),
                },
            )
        )
        .mappings()
        .first()
    )

    await audit.record(
        conn,
        action="timesheet.entry_added",
        resource_type="timesheet",
        resource_id=sheet["id"],
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        new_values={
            "entry_id": str(row["id"]),
            "entry_date": str(entry_date),
            "hours": str(payload.get("hours", 0)),
        },
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_timesheet(
        conn, company_id=company_id, public_id=public_id, actor_user_id=actor_user_id
    )


async def update_entry(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    entry_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    sheet = await _timesheet_row(conn, company_id=company_id, public_id=public_id, lock=True)
    _assert_editable(sheet, actor_user_id)

    fields = {
        k: v
        for k, v in payload.items()
        if k
        in {
            "entry_date",
            "start_time",
            "end_time",
            "break_minutes",
            "hours",
            "is_billable",
            "work_description",
            "project_task",
        }
        and v is not None
    }
    if not fields:
        return await get_timesheet(
            conn, company_id=company_id, public_id=public_id, actor_user_id=actor_user_id
        )

    entry_date = fields.get("entry_date")
    if entry_date is not None and not (sheet["period_start"] <= entry_date <= sheet["period_end"]):
        raise ValidationError(
            "That date falls outside this timesheet's period.",
            details={
                "reason": "ENTRY_OUTSIDE_PERIOD",
                "period_start": str(sheet["period_start"]),
                "period_end": str(sheet["period_end"]),
            },
        )

    assignments = ", ".join(f"{col} = :{col}" for col in fields)
    updated = await conn.execute(
        text(
            f"""
            UPDATE public.timesheet_entries SET {assignments}
             WHERE id = :eid AND timesheet_id = :tid
            """  # noqa: S608 - allowlisted columns
        ),
        {**fields, "eid": entry_id, "tid": sheet["id"]},
    )
    if updated.rowcount == 0:
        raise ResourceNotFoundError("Timesheet entry not found.")

    await audit.record(
        conn,
        action="timesheet.entry_updated",
        resource_type="timesheet",
        resource_id=sheet["id"],
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        new_values={k: _jsonable(v) for k, v in fields.items()},
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_timesheet(
        conn, company_id=company_id, public_id=public_id, actor_user_id=actor_user_id
    )


async def delete_entry(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    entry_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    reason: str,
) -> dict[str, Any]:
    sheet = await _timesheet_row(conn, company_id=company_id, public_id=public_id, lock=True)
    _assert_editable(sheet, actor_user_id)

    deleted = await conn.execute(
        text("DELETE FROM public.timesheet_entries WHERE id = :eid AND timesheet_id = :tid"),
        {"eid": entry_id, "tid": sheet["id"]},
    )
    if deleted.rowcount == 0:
        raise ResourceNotFoundError("Timesheet entry not found.")

    await audit.record(
        conn,
        action="timesheet.entry_removed",
        resource_type="timesheet",
        resource_id=sheet["id"],
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        reason=reason,
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_timesheet(
        conn, company_id=company_id, public_id=public_id, actor_user_id=actor_user_id
    )


async def submit_timesheet(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    notes: str | None = None,
) -> dict[str, Any]:
    """DRAFT|REJECTED -> SUBMITTED.

    The `trg_timesheets_chain` trigger materialises the approval steps from the
    contract's `timesheet_approval_chain` and moves the sheet to UNDER_REVIEW. If
    the contract defines no chain, the sheet stays SUBMITTED for a direct approval.
    """
    sheet = await _timesheet_row(conn, company_id=company_id, public_id=public_id, lock=True)
    status = str(sheet["status"])
    if status not in {"DRAFT", "REJECTED"}:
        raise InvalidStateTransitionError(
            f"A timesheet in status {status} cannot be submitted.",
            details={"status": status},
        )
    if str(sheet["user_id"]) != str(actor_user_id):
        raise BusinessRuleViolationError(
            "Only the owner of a timesheet may submit it.",
            details={"reason": "TIMESHEET_NOT_OWNED"},
        )

    empty = await conn.execute(
        text("SELECT count(*) FROM public.timesheet_entries WHERE timesheet_id = :tid"),
        {"tid": sheet["id"]},
    )
    if as_decimal(empty.scalar()) == 0:
        raise BusinessRuleViolationError(
            "Add at least one time entry before submitting this timesheet.",
            details={"reason": "TIMESHEET_EMPTY"},
        )

    chain = await conn.execute(
        text(
            """
            SELECT COALESCE(jsonb_array_length(timesheet_approval_chain -> 'steps'), 0)
              FROM public.contracts WHERE id = :cid
            """
        ),
        {"cid": sheet["contract_id"]},
    )
    has_chain = as_decimal(chain.scalar()) > 0

    if not has_chain:
        # Single-approver contracts still need a chain so the approval action is
        # recorded as a decision rather than a bare status flip.
        await conn.execute(
            text(
                """
                INSERT INTO public.timesheet_approvals
                  (timesheet_id, step_no, company_id, required_permission, status, due_at)
                VALUES (:tid, 1, :cid, 'timesheets.approve', 'PENDING', current_date + 3)
                ON CONFLICT (timesheet_id, step_no) DO NOTHING
                """
            ),
            {"tid": sheet["id"], "cid": company_id},
        )

    await conn.execute(
        text(
            "UPDATE public.timesheets SET status = 'SUBMITTED', rejection_reason = NULL"
            " WHERE id = :rid"
        ),
        {"rid": sheet["id"]},
    )
    # Move straight into review when there is a chain to walk.
    if has_chain:
        await conn.execute(
            text(
                "UPDATE public.timesheets SET status = 'UNDER_REVIEW', current_step = 1"
                " WHERE id = :rid AND status = 'SUBMITTED'"
            ),
            {"rid": sheet["id"]},
        )

    await audit.record(
        conn,
        action="timesheet.submitted",
        resource_type="timesheet",
        resource_id=sheet["id"],
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        old_values={"status": status},
        new_values={
            "status": "UNDER_REVIEW" if has_chain else "SUBMITTED",
            "total_hours": str(sheet["total_hours"]),
            "billable_hours": str(sheet["billable_hours"]),
        },
        reason=notes,
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_timesheet(
        conn, company_id=company_id, public_id=public_id, actor_user_id=actor_user_id
    )


async def decide_timesheet(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    step_no: int | None,
    decision: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    notes: str | None,
) -> dict[str, Any]:
    """Approve or reject one approval step.

    `advance_timesheet_on_final_approval` promotes the timesheet to APPROVED once
    the last pending step is decided, and to REJECTED on the first rejection.
    """
    sheet = await _timesheet_row(conn, company_id=company_id, public_id=public_id, lock=True)
    status = str(sheet["status"])
    if status not in {"SUBMITTED", "UNDER_REVIEW"}:
        raise InvalidStateTransitionError(
            f"A timesheet in status {status} is not awaiting approval.",
            details={"status": status, "decidable_in": ["SUBMITTED", "UNDER_REVIEW"]},
        )

    if step_no is None:
        target = (
            (
                await conn.execute(
                    text(
                        """
                        SELECT step_no FROM public.timesheet_approvals
                         WHERE timesheet_id = :tid AND status = 'PENDING'
                         ORDER BY step_no LIMIT 1
                        """
                    ),
                    {"tid": sheet["id"]},
                )
            )
            .mappings()
            .first()
        )
        if target is None:
            raise ResourceNotFoundError("No pending approval step on this timesheet.")
        step_no = int(target["step_no"])

    updated = await conn.execute(
        text(
            """
            UPDATE public.timesheet_approvals
               SET status = :decision, decided_at = now(), notes = :notes
             WHERE timesheet_id = :tid AND step_no = :step AND status = 'PENDING'
            RETURNING step_no
            """
        ),
        {"decision": decision, "notes": notes, "tid": sheet["id"], "step": step_no},
    )
    if updated.mappings().first() is None:
        raise ResourceNotFoundError("That approval step is not pending.")

    await audit.record(
        conn,
        action=f"timesheet.{decision.lower()}",
        resource_type="timesheet",
        resource_id=sheet["id"],
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        new_values={"step_no": step_no, "decision": decision},
        reason=notes,
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_timesheet(
        conn, company_id=company_id, public_id=public_id, actor_user_id=actor_user_id
    )


async def lock_timesheet(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    notes: str | None,
) -> dict[str, Any]:
    """APPROVED -> LOCKED. Once locked, no entry may change (rule 6)."""
    sheet = await _timesheet_row(conn, company_id=company_id, public_id=public_id, lock=True)
    if str(sheet["status"]) != "APPROVED":
        raise InvalidStateTransitionError(
            f"A timesheet in status {sheet['status']} cannot be locked.",
            details={"status": sheet["status"], "lockable_in": ["APPROVED"]},
        )

    already = await conn.execute(
        text(
            """
            SELECT 1 FROM public.invoice_items i WHERE i.source_timesheet_id = :tid
            """
        ),
        {"tid": sheet["id"]},
    )
    if already.scalar() is None:
        pass  # nothing invoiced yet; locking is still fine, billing follows later

    await conn.execute(
        text("UPDATE public.timesheets SET status = 'LOCKED' WHERE id = :rid"), {"rid": sheet["id"]}
    )
    await audit.record(
        conn,
        action="timesheet.locked",
        resource_type="timesheet",
        resource_id=sheet["id"],
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        old_values={"status": "APPROVED"},
        new_values={"status": "LOCKED"},
        reason=notes,
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_timesheet(
        conn, company_id=company_id, public_id=public_id, actor_user_id=actor_user_id
    )


async def revise_timesheet(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    reason: str,
    notes: str | None,
) -> dict[str, Any]:
    """The controlled revision path.

    An approved or locked sheet is never edited in place. A correction is recorded
    as an explicit adjustment sheet that supersedes it, and the original is
    released back to DRAFT so the author can amend and resubmit.
    """
    sheet = await _timesheet_row(conn, company_id=company_id, public_id=public_id, lock=True)
    status = str(sheet["status"])
    if status not in {"APPROVED", "LOCKED"}:
        raise InvalidStateTransitionError(
            "Only an approved or locked timesheet needs a revision.",
            details={"status": status, "revisable_in": ["APPROVED", "LOCKED"]},
        )

    invoiced = await conn.execute(
        text("SELECT count(*) FROM public.invoice_items WHERE source_timesheet_id = :tid"),
        {"tid": sheet["id"]},
    )
    if as_decimal(invoiced.scalar()) > 0:
        raise BusinessRuleViolationError(
            "This timesheet is already on an invoice. Raise a credit note instead.",
            details={"reason": "TIMESHEET_ALREADY_INVOICED"},
        )

    await conn.execute(
        text(
            """
            UPDATE public.timesheet_revisions
               SET change_type = 'ADJUSTED', summary = :summary, reason = :reason
             WHERE timesheet_id = :tid AND revision_no = (
                   SELECT max(revision_no) FROM public.timesheet_revisions WHERE timesheet_id = :tid
             )
            """
        ),
        {"summary": f"Revision requested: {reason}", "reason": reason, "tid": sheet["id"]},
    )

    # APPROVED -> LOCKED -> DRAFT is not a legal path in the state machine, so the
    # reopen is recorded as an explicit supersede in the revision trail and the
    # sheet returns to DRAFT for amendment.
    await conn.execute(
        text(
            """
            UPDATE public.timesheets
               SET status = 'DRAFT', locked_at = NULL, locked_by = NULL,
                   approved_at = NULL, is_adjustment = true, adjustment_of = :rid
             WHERE id = :rid
            """
        ),
        {"rid": sheet["id"]},
    )

    await audit.record(
        conn,
        action="timesheet.revision_requested",
        resource_type="timesheet",
        resource_id=sheet["id"],
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        old_values={"status": status},
        new_values={"status": "DRAFT", "revision_of": public_id},
        reason=reason,
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_timesheet(
        conn, company_id=company_id, public_id=public_id, actor_user_id=actor_user_id
    )


# =============================================================================
# leave
# =============================================================================
async def list_leave_policies(
    conn: AsyncConnection, *, company_id: uuid.UUID
) -> list[dict[str, Any]]:
    rows = (
        (
            await conn.execute(
                text(
                    """
                    SELECT lp.public_id, lp.name, lp.leave_type, lp.accrual_method,
                           lp.accrual_rate, lp.max_balance, lp.carry_forward_limit,
                           lp.requires_approval, lp.min_notice_days, lp.max_consecutive_days,
                           lp.allow_negative_balance, lp.effective_from, lp.effective_to,
                           lp.is_active
                      FROM public.leave_policies lp
                     WHERE lp.company_id = :cid
                     ORDER BY lp.leave_type, lp.name
                    """
                ),
                {"cid": company_id},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def create_leave_policy(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    row = (
        (
            await conn.execute(
                text(
                    """
                    INSERT INTO public.leave_policies
                      (company_id, name, leave_type, accrual_method, accrual_rate,
                       max_balance, carry_forward_limit, requires_approval,
                       min_notice_days, max_consecutive_days, allow_negative_balance,
                       effective_from, effective_to, created_by)
                    VALUES
                      (:cid, :name, :leave_type, :accrual_method, :accrual_rate,
                       :max_balance, :carry_forward, :requires_approval,
                       :min_notice, :max_consecutive, :allow_negative,
                       :effective_from, :effective_to, :actor)
                    RETURNING public_id
                    """
                ),
                {
                    "cid": company_id,
                    "name": payload["name"],
                    "leave_type": payload.get("leave_type", "ANNUAL"),
                    "accrual_method": payload.get("accrual_method", "MONTHLY"),
                    "accrual_rate": payload.get("accrual_rate", 0),
                    "max_balance": payload.get("max_balance"),
                    "carry_forward": payload.get("carry_forward_limit"),
                    "requires_approval": payload.get("requires_approval", True),
                    "min_notice": payload.get("min_notice_days", 0),
                    "max_consecutive": payload.get("max_consecutive_days"),
                    "allow_negative": payload.get("allow_negative_balance", False),
                    "effective_from": payload.get("effective_from") or utc_today(),
                    "effective_to": payload.get("effective_to"),
                    "actor": actor_user_id,
                },
            )
        )
        .mappings()
        .first()
    )
    await audit.record(
        conn,
        action="leave_policy.created",
        resource_type="leave_policy",
        resource_public_id=str(row["public_id"]),
        company_id=company_id,
        actor_user_id=actor_user_id,
        new_values={"name": payload["name"], "leave_type": payload.get("leave_type")},
        request_id=request_id,
        ip_address=ip_address,
    )
    policies = await list_leave_policies(conn, company_id=company_id)
    return next(p for p in policies if p["public_id"] == str(row["public_id"]))


async def list_leave_requests(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    mine: bool = False,
    user_public_id: str | None = None,
    status: str | None = None,
    limit: int,
    cursor_keys: dict[str, str],
) -> list[dict[str, Any]]:
    where = ["r.company_id = :cid"]
    params: dict[str, Any] = {"cid": company_id, "limit": limit + 1}

    if mine:
        where.append("r.user_id = :uid")
        params["uid"] = actor_user_id
    elif user_public_id:
        resolved = await resolve_user_public_id(conn, user_public_id, company_id=company_id)
        where.append("r.user_id = :uid")
        params["uid"] = resolved
    if status:
        where.append("r.status = :status")
        params["status"] = status
    if cursor_keys.get("start_date"):
        where.append("(r.start_date, r.public_id) < (:cur_start, :cur_public)")
        params["cur_start"] = cursor_keys["start_date"]
        params["cur_public"] = cursor_keys["public_id"]

    rows = (
        (
            await conn.execute(
                text(
                    f"""
                    SELECT r.public_id, r.user_id, u.public_id AS user_public_id,
                           NULLIF(TRIM(u.first_name || ' ' || u.last_name), '') AS user_name,
                           lp.public_id AS policy_public_id, lp.name AS policy_name,
                           lp.leave_type, r.start_date, r.end_date, r.total_days,
                           r.reason, r.status, r.approver_user_id::text AS approver_user_id,
                           r.decided_at, r.decision_notes, r.created_at
                      FROM public.leave_requests r
                      JOIN public.users u ON u.id = r.user_id
                      JOIN public.leave_policies lp ON lp.id = r.leave_policy_id
                     WHERE {" AND ".join(where)}
                     ORDER BY r.start_date DESC, r.public_id DESC
                     LIMIT :limit
                    """  # noqa: S608
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    out = []
    approver_ids = [uuid.UUID(str(r["approver_user_id"])) for r in rows if r["approver_user_id"]]
    approvers = await _public_id_map(conn, approver_ids) if approver_ids else {}
    for r in rows:
        entry = dict(r)
        entry["user_id"] = entry.pop("user_public_id")
        if entry.get("approver_user_id"):
            entry["approver_user_id"] = approvers.get(str(entry["approver_user_id"]))
        out.append(entry)
    return out


async def _public_id_map(conn: AsyncConnection, user_ids: Sequence[uuid.UUID]) -> dict[str, str]:
    """user uuid -> `U...` public id, in one query rather than one per row."""
    if not user_ids:
        return {}
    rows = (
        (
            await conn.execute(
                text(
                    """
                    SELECT id::text AS id, public_id FROM public.users
                     WHERE id = ANY(CAST(:ids AS uuid[]))
                    """
                ),
                {"ids": [str(u) for u in user_ids]},
            )
        )
        .mappings()
        .all()
    )
    return {str(r["id"]): str(r["public_id"]) for r in rows}


async def get_leave_request(
    conn: AsyncConnection, *, company_id: uuid.UUID, public_id: str
) -> dict[str, Any]:
    row = (
        (
            await conn.execute(
                text(
                    """
                    SELECT r.public_id, r.start_date, r.end_date, r.total_days, r.reason,
                           r.status, r.decided_at, r.decision_notes, r.created_at,
                           u.public_id AS user_public_id,
                           NULLIF(TRIM(u.first_name || ' ' || u.last_name), '') AS user_name,
                           lp.public_id AS policy_public_id, lp.name AS policy_name,
                           lp.leave_type
                      FROM public.leave_requests r
                      JOIN public.users u ON u.id = r.user_id
                      JOIN public.leave_policies lp ON lp.id = r.leave_policy_id
                     WHERE r.public_id = :pid AND r.company_id = :cid
                    """
                ),
                {"pid": public_id, "cid": company_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ResourceNotFoundError("Leave request not found.")
    data = dict(row)
    data["user_id"] = data.pop("user_public_id")
    return data


async def leave_balances(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    user_public_id: str | None,
    year: int | None,
) -> list[dict[str, Any]]:
    target = actor_user_id
    if user_public_id:
        target = await resolve_user_public_id(conn, user_public_id, company_id=company_id)

    rows = (
        (
            await conn.execute(
                text(
                    """
                    SELECT lp.public_id AS policy_public_id, lp.name AS policy_name,
                           lp.leave_type, lb.year,
                           lb.entitled, lb.accrued, lb.taken, lb.pending,
                           lb.carried_over, lb.expires_on,
                           app.leave_available(
                             CAST(:uid AS uuid), CAST(:cid AS uuid), lb.leave_policy_id, lb.year
                           ) AS available
                      FROM public.leave_balances lb
                      JOIN public.leave_policies lp ON lp.id = lb.leave_policy_id
                     WHERE lb.user_id = CAST(:uid AS uuid) AND lb.company_id = CAST(:cid AS uuid)
                       AND (:year IS NULL OR lb.year = :year)
                     ORDER BY lb.year DESC, lp.leave_type
                    """
                ),
                {"uid": target, "cid": company_id, "year": year},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def create_leave_request(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    policy = await resolve_scoped(conn, "leave_policies", payload["leave_policy_id"], company_id)
    target = actor_user_id
    if payload.get("user_id"):
        target = await resolve_user_public_id(conn, payload["user_id"], company_id=company_id)

    start = payload["start_date"]
    end = payload["end_date"]
    if end < start:
        raise ValidationError("The leave end date must not precede the start date.")

    total_days = as_decimal(payload.get("total_days"), "0") or _business_days(start, end)

    row = (
        (
            await conn.execute(
                text(
                    """
                    INSERT INTO public.leave_requests
                      (user_id, company_id, leave_policy_id, start_date, end_date,
                       total_days, reason, status)
                    VALUES
                      (:uid, :cid, CAST(:pid AS uuid), :start, :end,
                       :days, :reason, 'PENDING')
                    RETURNING public_id
                    """
                ),
                {
                    "uid": target,
                    "cid": company_id,
                    "pid": policy["id"],
                    "start": start,
                    "end": end,
                    "days": total_days,
                    "reason": payload.get("reason"),
                },
            )
        )
        .mappings()
        .first()
    )

    await conn.execute(
        text(
            """
            INSERT INTO public.leave_balances (user_id, company_id, leave_policy_id, year, pending)
            VALUES (:uid, :cid, :pid, :year, :days)
            ON CONFLICT (user_id, company_id, leave_policy_id, year)
            DO UPDATE SET pending = public.leave_balances.pending + EXCLUDED.pending,
                          updated_at = now()
            """
        ),
        {
            "uid": target,
            "cid": company_id,
            "pid": policy["id"],
            "year": start.year,
            "days": total_days,
        },
    )

    await audit.record(
        conn,
        action="leave.requested",
        resource_type="leave_request",
        resource_public_id=str(row["public_id"]),
        company_id=company_id,
        actor_user_id=actor_user_id,
        new_values={
            "policy_public_id": payload["leave_policy_id"],
            "start_date": str(start),
            "end_date": str(end),
            "total_days": str(total_days),
        },
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_leave_request(conn, company_id=company_id, public_id=str(row["public_id"]))


async def decide_leave_request(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    decision: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    notes: str | None,
) -> dict[str, Any]:
    before = await get_leave_request(conn, company_id=company_id, public_id=public_id)
    if str(before["status"]) != "PENDING":
        raise InvalidStateTransitionError(
            f"A leave request in status {before['status']} has already been decided.",
            details={"status": before["status"]},
        )
    target = "APPROVED" if decision == "APPROVED" else "REJECTED"

    row = (
        (
            await conn.execute(
                text(
                    "SELECT id::text FROM public.leave_requests"
                    " WHERE public_id = :pid AND company_id = :cid"
                ),
                {"pid": public_id, "cid": company_id},
            )
        )
        .mappings()
        .first()
    )

    updated = await conn.execute(
        text(
            """
            UPDATE public.leave_requests
               SET status = :target, approver_user_id = :actor, decision_notes = :notes
             WHERE id = :rid
            RETURNING public_id
            """
        ),
        {"target": target, "actor": actor_user_id, "notes": notes, "rid": row["id"]},
    )
    if updated.mappings().first() is None:
        raise ResourceNotFoundError("Leave request not found.")

    # Approved leave takes the person off the assignment, so nobody can bill those
    # days; `add_entry` also refuses the overlapping dates as a second line.
    if target == "APPROVED":
        await conn.execute(
            text(
                """
                UPDATE public.assignments a
                   SET status = 'ON_LEAVE'
                 WHERE a.user_id = (SELECT user_id FROM public.leave_requests WHERE id = :rid)
                   AND a.status = 'ACTIVE'
                   AND a.start_date <= (SELECT end_date FROM public.leave_requests WHERE id = :rid)
                   AND (a.end_date IS NULL
                        OR a.end_date >= (SELECT start_date FROM public.leave_requests
                                          WHERE id = :rid))
                """
            ),
            {"rid": row["id"]},
        )

    await audit.record(
        conn,
        action=f"leave.{target.lower()}",
        resource_type="leave_request",
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        old_values={"status": "PENDING"},
        new_values={"status": target},
        reason=notes,
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_leave_request(conn, company_id=company_id, public_id=public_id)


async def cancel_leave_request(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    reason: str,
) -> dict[str, Any]:
    before = await get_leave_request(conn, company_id=company_id, public_id=public_id)
    if str(before["status"]) not in {"DRAFT", "PENDING"}:
        raise InvalidStateTransitionError(
            f"A leave request in status {before['status']} cannot be cancelled.",
            details={"status": before["status"]},
        )

    row = (
        (
            await conn.execute(
                text(
                    "SELECT id::text, user_id, leave_policy_id, total_days,"
                    " start_date FROM public.leave_requests"
                    " WHERE public_id = :pid AND company_id = :cid"
                ),
                {"pid": public_id, "cid": company_id},
            )
        )
        .mappings()
        .first()
    )
    await conn.execute(
        text("UPDATE public.leave_requests SET status = 'CANCELLED' WHERE id = :rid"),
        {"rid": row["id"]},
    )
    await conn.execute(
        text(
            """
            UPDATE public.leave_balances
               SET pending = GREATEST(pending - :days, 0), updated_at = now()
             WHERE user_id = :uid AND company_id = :cid AND leave_policy_id = :pid AND year = :year
            """
        ),
        {
            "days": row["total_days"],
            "uid": row["user_id"],
            "cid": company_id,
            "pid": row["leave_policy_id"],
            "year": row["start_date"].year,
        },
    )
    await conn.execute(
        text(
            """
            UPDATE public.assignments SET status = 'ACTIVE'
             WHERE user_id = :uid AND company_id = :cid AND status = 'ON_LEAVE'
            """
        ),
        {"uid": row["user_id"], "cid": company_id},
    )

    await audit.record(
        conn,
        action="leave.cancelled",
        resource_type="leave_request",
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        old_values={"status": before["status"]},
        new_values={"status": "CANCELLED"},
        reason=reason,
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_leave_request(conn, company_id=company_id, public_id=public_id)


def _business_days(start: date, end: date) -> Decimal:
    """Weekdays between two dates, inclusive. Holidays need a calendar table."""
    if end < start:
        return ZERO
    total = 0
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            total += 1
        cursor += timedelta(days=1)
    return Decimal(total)
