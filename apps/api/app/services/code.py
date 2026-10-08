"""CODE module: projects, project roles, SOWs, contracts and contract roles.

Business rules that matter, and where each one is enforced:

  * A contract may only reference a SOW belonging to the same project
    (`app.assert_contract_insert`).
  * A contract role must reference a project role on that contract's project
    (`app.assert_contract_role_project`, added in 0015).
  * Status machines are validated by the database
    (`app.assert_contract_transition`, `app.on_sow_status_change`), and this
    module only ever asks for a transition the database will accept.
  * Rates and totals are never taken from the request body. A rate supplied here
    is a *proposal*; `app.compute_entry` and `app.compute_invoice_item` recompute
    the money from the authoritative contract role.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.errors import (
    BusinessRuleViolationError,
    InvalidStateTransitionError,
    ResourceNotFoundError,
    ValidationError,
)
from app.core.logging import get_logger
from app.services import audit
from app.services.lookup import (
    as_decimal,
    json_or_empty,
    resolve_company_public_id,
    resolve_personal,
    resolve_scoped,
    resolve_user_public_id,
)

logger = get_logger(__name__)

ZERO = Decimal("0")

# Contract status graph, mirrored from `app.assert_contract_transition` so the API
# can publish `allowed_transitions` without a round trip.
CONTRACT_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "DRAFT": ("SENT", "DECLINED", "CLOSED"),
    "SENT": ("PENDING_ACCEPTANCE", "ACCEPTED", "DECLINED", "CLOSED"),
    "PENDING_ACCEPTANCE": ("ACCEPTED", "DECLINED", "CLOSED"),
    "ACCEPTED": ("ACTIVE", "DECLINED", "CLOSED"),
    "ACTIVE": ("EXPIRED", "TERMINATED", "CLOSED"),
    "DECLINED": ("CLOSED",),
    "EXPIRED": ("CLOSED", "ACTIVE"),
    "TERMINATED": ("CLOSED",),
    "CLOSED": (),
}

SOW_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "DRAFT": ("PENDING_APPROVAL", "CLOSED"),
    "PENDING_APPROVAL": ("ACTIVE", "REJECTED", "DRAFT", "CLOSED"),
    "ACTIVE": ("EXPIRED", "TERMINATED", "CLOSED"),
    "REJECTED": ("DRAFT", "CLOSED"),
    "EXPIRED": ("CLOSED",),
    "TERMINATED": ("CLOSED",),
    "CLOSED": (),
}

# The contract states that can carry commercial terms and therefore may be edited.
CONTRACT_EDITABLE = frozenset({"DRAFT"})
SOW_EDITABLE = frozenset({"DRAFT"})


# =============================================================================
# helpers
# =============================================================================
async def _company_public_id(conn: AsyncConnection, company_id: uuid.UUID) -> str:
    row = (
        (
            await conn.execute(
                text("SELECT public_id FROM public.companies WHERE id = CAST(:cid AS uuid)"),
                {"cid": company_id},
            )
        )
        .mappings()
        .first()
    )
    return str(row["public_id"]) if row else ""


def _assert_transition(
    graph: dict[str, tuple[str, ...]], current: str, target: str, label: str
) -> None:
    if target not in graph.get(current, ()):
        raise InvalidStateTransitionError(
            f"A {label} cannot move from {current} to {target}.",
            details={
                "from": current,
                "to": target,
                "allowed": list(graph.get(current, ())),
            },
        )


#: Project lifecycle (§10). Terminal states are read-only: COMPLETED,
#: CANCELLED and CLOSED accept no further transition.
PROJECT_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "DRAFT": ("PLANNING", "ACTIVE", "CANCELLED"),
    "PLANNING": ("ACTIVE", "CANCELLED"),
    "ACTIVE": ("ON_HOLD", "COMPLETED", "CANCELLED"),
    "ON_HOLD": ("ACTIVE", "CANCELLED"),
    "COMPLETED": (),
    "CANCELLED": (),
    "CLOSED": (),
}


def _num(value: Any) -> float:
    return float(as_decimal(value))


# =============================================================================
# projects
# =============================================================================
_PROJECT_SELECT = """
    SELECT p.id, p.public_id, p.company_id, p.name, p.description, p.project_type,
           p.category, p.status, p.start_date, p.estimated_end_date, p.estimated_hours,
           p.estimated_budget, p.currency, p.billing_basis, p.billing_frequency,
           p.payment_terms_days, p.health_score, p.owner_user_id, p.metadata,
           p.created_at, p.updated_at,
           ou.public_id AS owner_public_id,
           NULLIF(TRIM(ou.first_name || ' ' || ou.last_name), '') AS owner_name,
           cp.public_id AS client_company_id,
           cp.name AS client_name,
           COALESCE(pr.role_count, 0)      AS role_count,
           COALESCE(pr.open_role_count, 0)  AS open_role_count,
           COALESCE(sw.sow_count, 0)        AS sow_count,
           COALESCE(ct.contract_count, 0)   AS contract_count,
           COALESCE(ct.active_contract_count, 0) AS active_contract_count,
           COALESCE(iv.invoiced_total, 0)   AS invoiced_total,
           COALESCE(iv.outstanding_total, 0) AS outstanding_total,
           COALESCE(ts.timesheet_count, 0)  AS timesheet_count,
           COALESCE(tm.team_size, 0)        AS team_size,
           GREATEST(p.updated_at,
                    COALESCE(pr.last_role_at, p.created_at),
                    COALESCE(iv.last_invoice_at, p.created_at),
                    COALESCE(ts.last_sheet_at, p.created_at)) AS last_activity_at
      FROM public.projects p
      LEFT JOIN public.users ou ON ou.id = p.owner_user_id
      LEFT JOIN LATERAL (
            SELECT c.public_id, COALESCE(c.display_name, c.legal_name) AS name
              FROM public.sows s JOIN public.companies c ON c.id = s.counterparty_company_id
             WHERE s.project_id = p.id AND s.counterparty_company_id IS NOT NULL
               AND s.deleted_at IS NULL
             ORDER BY s.created_at LIMIT 1
      ) cp ON TRUE
      LEFT JOIN LATERAL (
            SELECT count(*) AS role_count,
                   count(*) FILTER (WHERE status = 'OPEN' AND allocated_count < required_count)
                       AS open_role_count,
                   max(updated_at) AS last_role_at
              FROM public.project_roles r WHERE r.project_id = p.id AND r.deleted_at IS NULL
      ) pr ON TRUE
      LEFT JOIN LATERAL (
            SELECT count(*) AS sow_count FROM public.sows s
             WHERE s.project_id = p.id AND s.deleted_at IS NULL
      ) sw ON TRUE
      LEFT JOIN LATERAL (
            SELECT count(*) AS contract_count,
                   count(*) FILTER (
                       WHERE c.status IN ('ACCEPTED','ACTIVE'))
                       AS active_contract_count
              FROM public.contracts c
             WHERE c.project_id = p.id AND c.deleted_at IS NULL
      ) ct ON TRUE
      LEFT JOIN LATERAL (
            SELECT sum(i.total_amount) FILTER (
                     WHERE i.status NOT IN ('DRAFT','PENDING','SUBMITTED','CANCELLED','REJECTED')
                   ) AS invoiced_total,
                   sum(i.balance_due) FILTER (
                     WHERE i.status NOT IN ('CANCELLED','REJECTED','REFUNDED')
                   ) AS outstanding_total,
                   max(i.created_at) AS last_invoice_at
              FROM public.invoices i
             WHERE i.project_id = p.id AND i.direction = 'RECEIVABLE' AND i.deleted_at IS NULL
      ) iv ON TRUE
      LEFT JOIN LATERAL (
            SELECT count(*) AS timesheet_count, max(t.submitted_at) AS last_sheet_at
              FROM public.timesheets t
             WHERE t.project_id = p.id
               AND t.status IN ('APPROVED','LOCKED')
      ) ts ON TRUE
      LEFT JOIN LATERAL (
            SELECT count(DISTINCT a.user_id) AS team_size
              FROM public.assignments a
             WHERE a.project_id = p.id AND a.status IN ('ACTIVE','ON_LEAVE')
      ) tm ON TRUE
"""


def _project_billing_status(invoiced: Any, outstanding: Any) -> str:
    if _num(invoiced) <= 0 and _num(outstanding) <= 0:
        return "NOT_STARTED"
    if _num(outstanding) <= 0:
        return "SETTLED"
    if _num(outstanding) < _num(invoiced):
        return "PARTIALLY_PAID"
    return "OUTSTANDING"


def _project_from_row(row: Any, company_public_id: str) -> dict[str, Any]:
    data = dict(row)
    data["company_id"] = company_public_id
    data["owner_user_id"] = data.get("owner_public_id")
    data["owner_name"] = data.get("owner_name")
    data["billing_status"] = _project_billing_status(
        data.get("invoiced_total"), data.get("outstanding_total")
    )
    for key in (
        "id",
        "owner_public_id",
        "owner_name",
        "client_company_id",
        "client_name",
        "estimated_hours",
        "estimated_budget",
        "health_score",
    ):
        data.pop(key, None)
    return data


async def list_projects(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    search: str | None,
    status: str | None,
    client_company_id: str | None,
    owner_user_id: str | None,
    cursor_keys: dict[str, str],
    limit: int,
) -> list[dict[str, Any]]:
    """Project list with search, filters and keyset pagination."""
    where = ["p.company_id = :cid", "p.deleted_at IS NULL"]
    params: dict[str, Any] = {"cid": company_id, "limit": limit + 1}

    if search:
        where.append("(p.name ILIKE :q OR p.description ILIKE :q)")
        params["q"] = f"%{search}%"
    if status:
        where.append("p.status = :status")
        params["status"] = status
    if owner_user_id:
        resolved = await resolve_user_public_id(conn, owner_user_id, company_id=company_id)
        where.append("p.owner_user_id = :owner")
        params["owner"] = resolved
    if client_company_id:
        resolved_company = await resolve_company_public_id(conn, client_company_id)
        where.append(
            "EXISTS (SELECT 1 FROM public.sows s"
            " WHERE s.project_id = p.id AND s.deleted_at IS NULL"
            "   AND s.counterparty_company_id = :client)"
        )
        params["client"] = resolved_company

    # Keyset: (updated_at, public_id) is the stable sort for the list view.
    if cursor_keys.get("updated_at"):
        where.append("(p.updated_at, p.public_id) < (:cur_updated, :cur_public)")
        params["cur_updated"] = cursor_keys["updated_at"]
        params["cur_public"] = cursor_keys["public_id"]

    rows = (
        (
            await conn.execute(
                text(
                    f"""
                    {_PROJECT_SELECT}
                     WHERE {" AND ".join(where)}
                     ORDER BY p.updated_at DESC, p.public_id DESC
                     LIMIT :limit
                    """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    company_public_id = await _company_public_id(conn, company_id)
    return [_project_from_row(r, company_public_id) for r in rows]


async def get_project(
    conn: AsyncConnection, *, company_id: uuid.UUID, public_id: str
) -> dict[str, Any]:
    row = (
        (
            await conn.execute(
                text(f"{_PROJECT_SELECT} WHERE p.public_id = :pid AND p.company_id = :cid"),
                {"pid": public_id, "cid": company_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ResourceNotFoundError("Project not found.")
    return _project_from_row(row, await _company_public_id(conn, company_id))


async def create_project(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    owner_id = (
        await resolve_user_public_id(conn, payload["owner_user_id"], company_id=company_id)
        if payload.get("owner_user_id")
        else actor_user_id
    )
    client_id = (
        await resolve_company_public_id(conn, payload["counterparty_company_id"])
        if payload.get("counterparty_company_id")
        else None
    )

    metadata = dict(payload.get("metadata") or {})
    if client_id is not None:
        # The client is not a project column; it is carried in metadata so the
        # project list can filter on it without a join, and mirrored onto the SOW.
        metadata["client_company_id"] = str(client_id)

    row = (
        (
            await conn.execute(
                text(
                    """
                    INSERT INTO public.projects
                      (company_id, name, description, project_type, category, status,
                       start_date, estimated_end_date, estimated_hours, estimated_budget,
                       currency, billing_basis, billing_frequency, payment_terms_days,
                       owner_user_id, metadata, created_by)
                    VALUES
                      (:cid, :name, :description, :project_type, :category, :status,
                       :start_date, :estimated_end_date, :estimated_hours, :estimated_budget,
                       :currency, :billing_basis, :billing_frequency, :payment_terms_days,
                       :owner, CAST(:metadata AS jsonb), :actor)
                    RETURNING public_id
                    """
                ),
                {
                    "cid": company_id,
                    "name": payload["name"],
                    "description": payload.get("description"),
                    "project_type": payload.get("project_type", "SERVICE"),
                    "category": payload.get("category", "COMPANY"),
                    "status": payload.get("status", "DRAFT"),
                    "start_date": payload.get("start_date"),
                    "estimated_end_date": payload.get("estimated_end_date"),
                    "estimated_hours": payload.get("estimated_hours"),
                    "estimated_budget": payload.get("estimated_budget"),
                    "currency": payload.get("currency", "USD"),
                    "billing_basis": payload.get("billing_basis", "TIMESHEET"),
                    "billing_frequency": payload.get("billing_frequency", "MONTHLY"),
                    "payment_terms_days": payload.get("payment_terms_days", 30),
                    "owner": owner_id,
                    "metadata": _json(metadata),
                    "actor": actor_user_id,
                },
            )
        )
        .mappings()
        .first()
    )

    await audit.record(
        conn,
        action="project.created",
        resource_type="project",
        resource_public_id=str(row["public_id"]),
        company_id=company_id,
        actor_user_id=actor_user_id,
        new_values={
            "name": payload["name"],
            "status": payload.get("status", "DRAFT"),
            "currency": payload.get("currency", "USD"),
            "billing_basis": payload.get("billing_basis", "TIMESHEET"),
        },
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_project(conn, company_id=company_id, public_id=str(row["public_id"]))


_PROJECT_UPDATABLE = frozenset(
    {
        "name",
        "description",
        "status",
        "start_date",
        "estimated_end_date",
        "estimated_hours",
        "estimated_budget",
        "currency",
        "billing_basis",
        "billing_frequency",
        "payment_terms_days",
        "owner_user_id",
        "metadata",
    }
)


async def update_project(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    changes: dict[str, Any],
) -> dict[str, Any]:
    before = await resolve_scoped(conn, "projects", public_id, company_id, columns="*", lock=True)

    current_status = str(before.get("status") or "")
    if current_status in {"COMPLETED", "CANCELLED", "CLOSED"}:
        raise InvalidStateTransitionError(
            f"A project in status {current_status} is read-only.",
            details={"status": current_status, "allowed": []},
        )

    updates = {k: v for k, v in changes.items() if k in _PROJECT_UPDATABLE and v is not None}
    if "status" in updates and str(updates["status"]) != current_status:
        _assert_transition(PROJECT_TRANSITIONS, current_status, str(updates["status"]), "project")
    if "owner_user_id" in updates:
        updates["owner_user_id"] = await resolve_user_public_id(
            conn, str(updates["owner_user_id"]), company_id=company_id
        )
    if "metadata" in updates:
        updates["metadata"] = _json(updates["metadata"])

    if not updates:
        return _project_from_row(before, await _company_public_id(conn, company_id))

    assignments = ", ".join(f"{col} = :{col}" for col in updates)
    await conn.execute(
        text(f"UPDATE public.projects SET {assignments} WHERE id = :rid"),  # noqa: S608 - allowlisted columns
        {**updates, "rid": before["id"]},
    )

    after = await resolve_scoped(conn, "projects", public_id, company_id)
    await audit.record(
        conn,
        action="project.updated",
        resource_type="project",
        resource_id=before["id"],
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        old_values={k: _jsonable(before.get(k)) for k in updates},
        new_values={k: _jsonable(after.get(k)) for k in updates},
        request_id=request_id,
        ip_address=ip_address,
    )
    return _project_from_row(after, await _company_public_id(conn, company_id))


async def archive_project(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    reason: str,
) -> None:
    before = await resolve_scoped(conn, "projects", public_id, company_id, columns="*", lock=True)

    live = await conn.execute(
        text(
            """
            SELECT
              (SELECT count(*) FROM public.contracts c
                WHERE c.project_id = :pid AND c.deleted_at IS NULL
                  AND c.status IN ('SENT','PENDING_ACCEPTANCE',
                                   'ACCEPTED','ACTIVE')) AS live_contracts,
              (SELECT count(*) FROM public.timesheets t
                WHERE t.project_id = :pid
                  AND t.status IN ('SUBMITTED','UNDER_REVIEW')) AS open_timesheets
            """
        ),
        {"pid": before["id"]},
    )
    counts: dict[str, Any] = dict(live.mappings().first() or {})
    if _num(counts.get("live_contracts")) > 0:
        raise BusinessRuleViolationError(
            "This project still has live contracts. Terminate or close them first.",
            details={"live_contracts": int(_num(counts.get("live_contracts")))},
        )
    if _num(counts.get("open_timesheets")) > 0:
        raise BusinessRuleViolationError(
            "This project still has timesheets awaiting approval.",
            details={"open_timesheets": int(_num(counts.get("open_timesheets")))},
        )

    await conn.execute(
        text(
            """
            UPDATE public.projects
               SET deleted_at = now(), status = 'CANCELLED'
             WHERE id = :rid
            """
        ),
        {"rid": before["id"]},
    )
    await audit.record(
        conn,
        action="project.archived",
        resource_type="project",
        resource_id=before["id"],
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        reason=reason,
        old_values={"status": before["status"]},
        new_values={"status": "CANCELLED", "deleted_at": "now()"},
        request_id=request_id,
        ip_address=ip_address,
    )


# =============================================================================
# project roles
# =============================================================================
_ROLE_SELECT = """
    SELECT r.id, r.public_id, r.project_id, r.company_id, r.title, r.description,
           r.required_count, r.allocated_count, r.required_skills, r.seniority,
           r.min_hourly_rate, r.max_hourly_rate, r.cost_rate, r.currency,
           r.billing_basis, r.allocation_pct, r.start_date, r.end_date, r.status,
           r.created_at, r.updated_at,
           p.public_id AS project_public_id, p.name AS project_name,
           COALESCE(cr.contracted_count, 0)  AS contracted_count,
           COALESCE(asg.active_assignments, 0) AS active_assignments,
           COALESCE(hrs.approved_hours, 0)     AS approved_hours_to_date
      FROM public.project_roles r
      JOIN public.projects p ON p.id = r.project_id
      LEFT JOIN LATERAL (
            SELECT count(*) AS contracted_count
              FROM public.contract_roles x JOIN public.contracts ct ON ct.id = x.contract_id
             WHERE x.project_role_id = r.id
               AND ct.status IN ('ACCEPTED','ACTIVE') AND ct.deleted_at IS NULL
      ) cr ON TRUE
      LEFT JOIN LATERAL (
            SELECT count(*) AS active_assignments
              FROM public.assignments a
             WHERE a.contract_role_id IN (
                   SELECT id FROM public.contract_roles WHERE project_role_id = r.id)
               AND a.status IN ('ACTIVE','ON_LEAVE')
      ) asg ON TRUE
      LEFT JOIN LATERAL (
            SELECT sum(t.billable_hours) AS approved_hours
              FROM public.timesheets t
              JOIN public.contract_roles x ON x.id = t.contract_role_id
             WHERE x.project_role_id = r.id
               AND t.status IN ('APPROVED','LOCKED')
      ) hrs ON TRUE
"""


def _role_from_row(row: Any) -> dict[str, Any]:
    data = dict(row)
    data.pop("id", None)
    data["project_id"] = data.pop("project_public_id")
    data.pop("project_name", None)
    # Utilisation compares approved hours against required headcount over the
    # elapsed weeks of the engagement. It is a capacity signal, not a target.
    required = as_decimal(data.get("required_count"), "1") or Decimal("1")
    hours = as_decimal(data.get("approved_hours_to_date"))
    data["utilisation_pct"] = (
        (hours / (required * Decimal("40")) * Decimal("100")).quantize(Decimal("0.01"))
        if required > 0
        else Decimal("0")
    )
    return data


async def list_project_roles(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    project_public_id: str | None = None,
    status: str | None = None,
    search: str | None = None,
    limit: int,
    cursor_keys: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    where = ["r.company_id = :cid", "r.deleted_at IS NULL"]
    params: dict[str, Any] = {"cid": company_id, "limit": limit + 1}

    if project_public_id:
        project = await resolve_scoped(conn, "projects", project_public_id, company_id)
        where.append("r.project_id = :pid")
        params["pid"] = project["id"]
    if status:
        where.append("r.status = :status")
        params["status"] = status
    if search:
        where.append("(r.title ILIKE :q OR r.description ILIKE :q)")
        params["q"] = f"%{search}%"
    if cursor_keys and cursor_keys.get("public_id"):
        where.append("r.public_id > :cur")
        params["cur"] = cursor_keys["public_id"]

    rows = (
        (
            await conn.execute(
                text(
                    f"""
                    {_ROLE_SELECT}
                     WHERE {" AND ".join(where)}
                     ORDER BY r.public_id ASC
                     LIMIT :limit
                    """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    return [_role_from_row(r) for r in rows]


async def get_project_role(
    conn: AsyncConnection, *, company_id: uuid.UUID, public_id: str
) -> dict[str, Any]:
    row = (
        (
            await conn.execute(
                text(f"{_ROLE_SELECT} WHERE r.public_id = :pid AND r.company_id = :cid"),
                {"pid": public_id, "cid": company_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ResourceNotFoundError("Project role not found.")
    return _role_from_row(row)


async def create_project_role(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    project_public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    project = await resolve_scoped(conn, "projects", project_public_id, company_id)

    row = (
        (
            await conn.execute(
                text(
                    """
                    INSERT INTO public.project_roles
                      (project_id, company_id, title, description, required_count,
                       required_skills, seniority, min_hourly_rate, max_hourly_rate,
                       cost_rate, currency, billing_basis, allocation_pct,
                       start_date, end_date, status, created_by)
                    VALUES
                      (:pid, :cid, :title, :description, :required_count,
                       CAST(:skills AS text[]), :seniority, :min_rate, :max_rate,
                       :cost_rate, :currency, :billing_basis, :allocation,
                       :start_date, :end_date, :status, :actor)
                    RETURNING public_id, id
                    """
                ),
                {
                    "pid": project["id"],
                    "cid": company_id,
                    "title": payload["title"],
                    "description": payload.get("description"),
                    "required_count": payload.get("required_count", 1),
                    "skills": _text_array(payload.get("required_skills") or []),
                    "seniority": payload.get("seniority"),
                    "min_rate": payload.get("min_hourly_rate"),
                    "max_rate": payload.get("max_hourly_rate"),
                    "cost_rate": payload.get("cost_rate"),
                    "currency": payload.get("currency", "USD"),
                    "billing_basis": payload.get("billing_basis", "TIMESHEET"),
                    "allocation": payload.get("allocation_pct", 100),
                    "start_date": payload.get("start_date"),
                    "end_date": payload.get("end_date"),
                    "status": payload.get("status", "OPEN"),
                    "actor": actor_user_id,
                },
            )
        )
        .mappings()
        .first()
    )

    # Recompute the derived allocation counter so the role's own status matches
    # whatever the project already committed.
    await conn.execute(
        text("SELECT app.refresh_role_allocation(CAST(:rid AS uuid))"), {"rid": row["id"]}
    )

    await audit.record(
        conn,
        action="project_role.created",
        resource_type="project_role",
        resource_public_id=str(row["public_id"]),
        company_id=company_id,
        actor_user_id=actor_user_id,
        new_values={
            "project_id": project_public_id,
            "title": payload["title"],
            "required_count": payload.get("required_count", 1),
        },
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_project_role(conn, company_id=company_id, public_id=str(row["public_id"]))


_ROLE_UPDATABLE = frozenset(
    {
        "title",
        "description",
        "required_count",
        "required_skills",
        "seniority",
        "min_hourly_rate",
        "max_hourly_rate",
        "cost_rate",
        "currency",
        "billing_basis",
        "allocation_pct",
        "start_date",
        "end_date",
        "status",
    }
)


async def update_project_role(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    changes: dict[str, Any],
    lock: bool = True,
) -> dict[str, Any]:
    before = await resolve_scoped(conn, "project_roles", public_id, company_id, lock=lock)
    updates = {k: v for k, v in changes.items() if k in _ROLE_UPDATABLE and v is not None}
    if not updates:
        return await get_project_role(conn, company_id=company_id, public_id=public_id)

    if "required_skills" in updates:
        updates["required_skills"] = _text_array(updates["required_skills"])

    assignments = ", ".join(f"{col} = :{col}" for col in updates)
    await conn.execute(
        text(f"UPDATE public.project_roles SET {assignments} WHERE id = :rid"),  # noqa: S608
        {**updates, "rid": before["id"]},
    )
    after = await resolve_scoped(conn, "project_roles", public_id, company_id)
    await audit.record(
        conn,
        action="project_role.updated",
        resource_type="project_role",
        resource_id=before["id"],
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        old_values={k: _jsonable(before.get(k)) for k in updates},
        new_values={k: _jsonable(after.get(k)) for k in updates},
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_project_role(conn, company_id=company_id, public_id=public_id)


async def deactivate_project_role(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    reason: str,
) -> None:
    before = await resolve_scoped(conn, "project_roles", public_id, company_id, lock=True)

    live = await conn.execute(
        text(
            """
            SELECT count(*) FROM public.assignments a
              JOIN public.contract_roles cr ON cr.id = a.contract_role_id
             WHERE cr.project_role_id = :rid AND a.status IN ('PENDING','ACTIVE','ON_LEAVE')
            """
        ),
        {"rid": before["id"]},
    )
    # A Result is single-use: scalar() twice raises ResourceClosedError.
    live_count = int(_num(live.scalar()))
    if live_count > 0:
        raise BusinessRuleViolationError(
            "This role still has live assignments. End them before closing the role.",
            details={"assignments": live_count},
        )

    await conn.execute(
        text(
            "UPDATE public.project_roles SET status = 'CLOSED', deleted_at = now() WHERE id = :rid"
        ),
        {"rid": before["id"]},
    )
    await audit.record(
        conn,
        action="project_role.deactivated",
        resource_type="project_role",
        resource_id=before["id"],
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        reason=reason,
        old_values={"status": before["status"]},
        new_values={"status": "CLOSED"},
        request_id=request_id,
        ip_address=ip_address,
    )


async def duplicate_project_role(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    title_suffix: str = " (copy)",
) -> dict[str, Any]:
    """Copy a role definition without its people or contract bindings."""
    source = await resolve_scoped(conn, "project_roles", public_id, company_id)
    return await create_project_role(
        conn,
        company_id=company_id,
        project_public_id=await _project_public_id(conn, source["project_id"]),
        actor_user_id=actor_user_id,
        request_id=request_id,
        ip_address=ip_address,
        payload={
            "title": f"{source['title']}{title_suffix}"[:160],
            "description": source.get("description"),
            "required_count": source["required_count"],
            "required_skills": list(source.get("required_skills") or []),
            "seniority": source.get("seniority"),
            "min_hourly_rate": source.get("min_hourly_rate"),
            "max_hourly_rate": source.get("max_hourly_rate"),
            "cost_rate": source.get("cost_rate"),
            "currency": source.get("currency") or "USD",
            "billing_basis": source.get("billing_basis") or "TIMESHEET",
            "allocation_pct": source.get("allocation_pct") or 100,
            "start_date": source.get("start_date"),
            "end_date": source.get("end_date"),
            "status": "OPEN",
        },
    )


# =============================================================================
# SOWs
# =============================================================================
_SOW_SELECT = """
    SELECT s.id, s.public_id, s.project_id, s.company_id, s.sow_type,
           s.counterparty_company_id, s.counterparty_user_id, s.title, s.description,
           s.status, s.start_date, s.end_date, s.currency, s.default_rate,
           s.billing_basis, s.billing_frequency, s.invoice_frequency,
           s.payment_terms_days, s.payment_method, s.special_conditions,
           s.max_total_amount, s.auto_generate_contracts, s.approved_by,
           s.approved_at, s.rejected_at, s.reject_reason,
           ua.public_id AS approved_by_public_id,
           ur.public_id AS rejected_by_public_id,
           s.document_id, s.created_at, s.updated_at,
           p.public_id AS project_public_id, p.name AS project_name,
           cp.public_id AS counterparty_company_public_id,
                   COALESCE(cp.display_name, cp.legal_name) AS counterparty_company_name,
           cu.public_id AS counterparty_user_public_id,
           NULLIF(TRIM(cu.first_name || ' ' || cu.last_name), '') AS counterparty_user_name,
           COALESCE(cc.contract_count, 0) AS contract_count,
           COALESCE(cc.contract_ids, '{}') AS contract_ids,
           s.metadata
      FROM public.sows s
      JOIN public.projects p ON p.id = s.project_id
      LEFT JOIN public.companies cp ON cp.id = s.counterparty_company_id
      LEFT JOIN public.users cu ON cu.id = s.counterparty_user_id
      LEFT JOIN public.users ua ON ua.id = s.approved_by
      LEFT JOIN public.users ur ON ur.id = s.rejected_by
      LEFT JOIN LATERAL (
            SELECT count(*) AS contract_count,
                   array_agg(c.public_id ORDER BY c.created_at) AS contract_ids
              FROM public.contracts c
             WHERE c.sow_id = s.id AND c.deleted_at IS NULL
      ) cc ON TRUE
"""


def _sow_from_row(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["project_id"] = data.pop("project_public_id")
    data["counterparty_company_id"] = data.pop("counterparty_company_public_id", None)
    data["counterparty_user_id"] = data.pop("counterparty_user_public_id", None)
    data.pop("project_name", None)
    data.pop("counterparty_company_name", None)
    data.pop("counterparty_user_name", None)
    data.pop("contract_count", None)
    data.pop("contract_ids", None)
    meta = json_or_empty(data.pop("metadata", None))
    data["scope"] = meta.get("scope")
    data["deliverables"] = meta.get("deliverables") or []
    data["milestones"] = meta.get("milestones") or []
    data["approved_by"] = data.pop("approved_by_public_id", None)
    data["rejected_by"] = data.pop("rejected_by_public_id", None)
    return data


async def _sow_roles(conn: AsyncConnection, sow_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        (
            await conn.execute(
                text(
                    """
                    SELECT pr.public_id AS project_role_id, pr.title AS role_title,
                           sr.quantity, sr.rate, sr.rate_type, sr.currency, sr.notes
                      FROM public.sow_roles sr
                      JOIN public.project_roles pr ON pr.id = sr.project_role_id
                     WHERE sr.sow_id = :sid
                     ORDER BY pr.public_id
                    """
                ),
                {"sid": sow_id},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def _sow_history(
    conn: AsyncConnection, public_id: str, company_id: uuid.UUID | None
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
                     WHERE a.resource_type = 'sow'
                       AND a.resource_public_id = :pid
                       AND a.company_id IS NOT DISTINCT FROM CAST(:cid AS uuid)
                     ORDER BY a.occurred_at DESC
                     LIMIT 100
                    """
                ),
                {"pid": public_id, "cid": company_id},
            )
        )
        .mappings()
        .all()
    )
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


async def get_sow(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    with_history: bool = False,
) -> dict[str, Any]:
    row = (
        (
            await conn.execute(
                text(f"{_SOW_SELECT} WHERE s.public_id = :pid AND s.company_id = :cid"),
                {"pid": public_id, "cid": company_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ResourceNotFoundError("SOW not found.")
    data = _sow_from_row(row)
    data["roles"] = await _sow_roles(conn, row["id"])
    data["contract_count"] = int(row["contract_count"] or 0)
    data["contract_ids"] = list(row["contract_ids"] or [])
    if with_history:
        data["history"] = await _sow_history(conn, public_id, company_id)
    return data


async def list_sows(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    project_public_id: str | None,
    status: str | None,
    search: str | None,
    limit: int,
    cursor_keys: dict[str, str],
) -> list[dict[str, Any]]:
    where = ["s.company_id = :cid", "s.deleted_at IS NULL"]
    params: dict[str, Any] = {"cid": company_id, "limit": limit + 1}

    if project_public_id:
        project = await resolve_scoped(conn, "projects", project_public_id, company_id)
        where.append("s.project_id = :pid")
        params["pid"] = project["id"]
    if status:
        where.append("s.status = :status")
        params["status"] = status
    if search:
        where.append("(s.title ILIKE :q OR s.description ILIKE :q)")
        params["q"] = f"%{search}%"
    if cursor_keys.get("updated_at"):
        where.append("(s.updated_at, s.public_id) < (:cur_updated, :cur_public)")
        params["cur_updated"] = cursor_keys["updated_at"]
        params["cur_public"] = cursor_keys["public_id"]

    rows = (
        (
            await conn.execute(
                text(
                    f"""
                    {_SOW_SELECT}
                     WHERE {" AND ".join(where)}
                     ORDER BY s.updated_at DESC, s.public_id DESC
                     LIMIT :limit
                    """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        data = _sow_from_row(r)
        data["contract_count"] = int(r["contract_count"] or 0)
        data["contract_ids"] = list(r["contract_ids"] or [])
        out.append(data)
    return out


async def create_sow(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    project_public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    project = await resolve_scoped(conn, "projects", project_public_id, company_id)

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
    document_id = None
    if payload.get("document_id"):
        doc = await resolve_scoped(conn, "documents", payload["document_id"], company_id)
        document_id = doc["id"]

    if counterparty_company is None and counterparty_user is None:
        # Default the counterparty to the project's recorded client, if any.
        recorded = (project.get("metadata") or {}).get("client_company_id")
        if recorded:
            counterparty_company = uuid.UUID(str(recorded))

    metadata = {
        "scope": payload.get("scope"),
        "deliverables": payload.get("deliverables") or [],
        "milestones": payload.get("milestones") or [],
    }

    row = (
        (
            await conn.execute(
                text(
                    """
                    INSERT INTO public.sows
                      (project_id, company_id, sow_type, counterparty_company_id,
                       counterparty_user_id, title, description, status, start_date, end_date,
                       currency, default_rate, billing_basis, billing_frequency,
                       invoice_frequency, payment_terms_days, payment_method,
                       special_conditions, max_total_amount, auto_generate_contracts,
                       document_id, metadata, created_by)
                    VALUES
                      (:pid, :cid, :sow_type, :counterparty_company,
                       :counterparty_user, :title, :description, 'DRAFT', :start_date, :end_date,
                       :currency, :default_rate, :billing_basis, :billing_frequency,
                       :invoice_frequency, :payment_terms_days, :payment_method,
                       :special_conditions, :max_total_amount, :auto_generate,
                       :document_id, CAST(:metadata AS jsonb), :actor)
                    RETURNING public_id
                    """
                ),
                {
                    "pid": project["id"],
                    "cid": company_id,
                    "sow_type": payload.get("sow_type", "COMPANY"),
                    "counterparty_company": counterparty_company,
                    "counterparty_user": counterparty_user,
                    "title": payload["title"],
                    "description": payload.get("description"),
                    "start_date": payload.get("start_date"),
                    "end_date": payload.get("end_date"),
                    "currency": payload.get("currency", "USD"),
                    "default_rate": payload.get("default_rate"),
                    "billing_basis": payload.get("billing_basis", "TIMESHEET"),
                    "billing_frequency": payload.get("billing_frequency", "MONTHLY"),
                    "invoice_frequency": payload.get("invoice_frequency", "MONTHLY"),
                    "payment_terms_days": payload.get("payment_terms_days", 30),
                    "payment_method": payload.get("payment_method"),
                    "special_conditions": payload.get("special_conditions"),
                    "max_total_amount": payload.get("max_total_amount"),
                    "auto_generate": payload.get("auto_generate_contracts", True),
                    "document_id": document_id,
                    "metadata": _json(metadata),
                    "actor": actor_user_id,
                },
            )
        )
        .mappings()
        .first()
    )

    created = await resolve_scoped(conn, "sows", str(row["public_id"]), company_id, columns="id")
    # The caller sends `R...` role identifiers. They have to be resolved to the
    # project's own role uuids before they reach the insert, exactly as update_sow
    # does; binding a public id as a uuid is what made create-with-roles fail.
    await _replace_sow_roles(
        conn,
        sow_id=uuid.UUID(str(created["id"])),
        requested=await _resolve_project_role_ids(
            conn,
            company_id=company_id,
            project_id=uuid.UUID(str(project["id"])),
            requested=payload.get("roles") or [],
        ),
    )

    await audit.record(
        conn,
        action="sow.created",
        resource_type="sow",
        resource_public_id=str(row["public_id"]),
        company_id=company_id,
        actor_user_id=actor_user_id,
        new_values={
            "project_id": project_public_id,
            "title": payload["title"],
            "currency": payload.get("currency", "USD"),
            "role_count": len(payload.get("roles") or []),
        },
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_sow(conn, company_id=company_id, public_id=str(row["public_id"]))


async def _resolve_project_role_ids(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    project_id: uuid.UUID,
    requested: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Map requested Role IDs to uuids, rejecting any role off this project.

    This is the rule that stops a SOW or contract inventing a role name: only a
    `R...` identifier that already belongs to the project is accepted.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in requested:
        role_public_id = item["project_role_id"]
        if role_public_id in seen:
            raise ValidationError(
                "The same project role appears twice.", details={"project_role_id": role_public_id}
            )
        seen.add(role_public_id)

        role = await resolve_scoped(conn, "project_roles", role_public_id, company_id)
        if str(role["project_id"]) != str(project_id):
            raise BusinessRuleViolationError(
                "That role does not belong to this project.",
                details={
                    "project_role_id": role_public_id,
                    "reason": "PROJECT_ROLE_NOT_ON_PROJECT",
                },
            )
        out.append({**item, "project_role_id": role["id"]})
    return out


async def _replace_sow_roles(
    conn: AsyncConnection, *, sow_id: uuid.UUID, requested: Sequence[dict[str, Any]]
) -> None:
    if not requested:
        await conn.execute(
            text("DELETE FROM public.sow_roles WHERE sow_id = :sid"), {"sid": sow_id}
        )
        return

    await conn.execute(text("DELETE FROM public.sow_roles WHERE sow_id = :sid"), {"sid": sow_id})
    for item in requested:
        await conn.execute(
            text(
                """
                INSERT INTO public.sow_roles
                  (sow_id, project_role_id, quantity, rate, rate_type, currency, notes)
                VALUES
                  (:sid, CAST(:prid AS uuid), :quantity, :rate, :rate_type, :currency, :notes)
                """
            ),
            {
                "sid": sow_id,
                "prid": item["project_role_id"],
                "quantity": item.get("quantity", 1),
                "rate": item.get("rate"),
                "rate_type": item.get("rate_type", "HOURLY"),
                "currency": item.get("currency", "USD"),
                "notes": item.get("notes"),
            },
        )


_SOW_UPDATABLE = frozenset(
    {
        "title",
        "description",
        "start_date",
        "end_date",
        "currency",
        "default_rate",
        "billing_basis",
        "billing_frequency",
        "invoice_frequency",
        "payment_terms_days",
        "payment_method",
        "special_conditions",
        "max_total_amount",
        "document_id",
    }
)


async def update_sow(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    changes: dict[str, Any],
) -> dict[str, Any]:
    before = await resolve_scoped(conn, "sows", public_id, company_id, lock=True)
    if str(before["status"]) not in SOW_EDITABLE:
        raise InvalidStateTransitionError(
            f"A SOW in status {before['status']} cannot be edited. Its commercial terms are fixed.",
            details={"status": before["status"], "editable_in": sorted(SOW_EDITABLE)},
        )

    updates = {k: v for k, v in changes.items() if k in _SOW_UPDATABLE and v is not None}
    payload = changes.model_dump(exclude_unset=True) if hasattr(changes, "model_dump") else changes

    metadata_before = json_or_empty(before.get("metadata"))
    metadata: dict[str, Any] = {}
    for key in ("scope", "deliverables", "milestones"):
        if payload.get(key) is not None:
            metadata[key] = payload[key]
    if "document_id" in updates:
        doc = await resolve_scoped(conn, "documents", str(updates["document_id"]), company_id)
        updates["document_id"] = doc["id"]

    if updates:
        assignments = ", ".join(f"{col} = :{col}" for col in updates)
        await conn.execute(
            text(f"UPDATE public.sows SET {assignments} WHERE id = :rid"),  # noqa: S608
            {**updates, "rid": before["id"]},
        )

    if metadata:
        merged = {**metadata_before, **metadata}
        await conn.execute(
            text(
                "UPDATE public.sows SET metadata ="
                " COALESCE(metadata, '{}'::jsonb) || CAST(:meta AS jsonb)"
                " WHERE id = :rid"
            ),
            {"meta": _json(merged), "rid": before["id"]},
        )

    if payload.get("roles") is not None:
        await _replace_sow_roles(
            conn,
            sow_id=before["id"],
            requested=await _resolve_project_role_ids(
                conn,
                company_id=company_id,
                project_id=before["project_id"],
                requested=payload["roles"],
            ),
        )

    after = await resolve_scoped(conn, "sows", public_id, company_id)
    await audit.record(
        conn,
        action="sow.updated",
        resource_type="sow",
        resource_id=before["id"],
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        old_values={
            k: _jsonable(before.get(k))
            for k in [*list(updates), "metadata"]
            if k in before or k == "metadata"
        },
        new_values={
            k: _jsonable(after.get(k))
            for k in [*list(updates), "metadata"]
            if k in after or k == "metadata"
        },
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_sow(conn, company_id=company_id, public_id=public_id)


async def transition_sow(
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
    before = await resolve_scoped(conn, "sows", public_id, company_id, lock=True)
    current = str(before["status"])
    _assert_transition(SOW_TRANSITIONS, current, target, "SOW")

    if target == "ACTIVE":
        # The database guard on_sow_role_change drops role bindings when a SOW is
        # closed, so make sure a contract-ready SOW still has roles to bill.
        roles = await conn.execute(
            text("SELECT count(*) FROM public.sow_roles WHERE sow_id = :sid"),
            {"sid": before["id"]},
        )
        if _num(roles.scalar()) == 0:
            raise BusinessRuleViolationError(
                "A SOW needs at least one project role before it can be activated.",
                details={"reason": "SOW_HAS_NO_ROLES"},
            )

    await conn.execute(
        text("UPDATE public.sows SET status = :target WHERE id = :rid"),
        {"target": target, "rid": before["id"]},
    )

    if target == "ACTIVE":
        await conn.execute(
            text(
                """
                UPDATE public.sows
                   SET approved_by = :actor, approved_at = now()
                 WHERE id = :rid AND approved_at IS NULL
                """
            ),
            {"actor": actor_user_id, "rid": before["id"]},
        )

    await audit.record(
        conn,
        action=f"sow.{target.lower()}",
        resource_type="sow",
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
    return await get_sow(conn, company_id=company_id, public_id=public_id, with_history=True)


async def _project_public_id(conn: AsyncConnection, project_id: Any) -> str:
    row = (
        (
            await conn.execute(
                text("SELECT public_id FROM public.projects WHERE id = CAST(:pid AS uuid)"),
                {"pid": project_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ResourceNotFoundError("Project not found.")
    return str(row["public_id"])


def _json(value: Any) -> str:
    import json

    return json.dumps(value, default=str)


def _text_array(values: Sequence[str]) -> str:
    """PostgreSQL text[] literal for a validated list of strings."""
    escaped = [v.replace("\\", "\\\\").replace('"', '\\"') for v in values]
    return "{" + ",".join(escaped) + "}"


def _jsonable(value: Any) -> Any:
    from datetime import date as _date
    from datetime import datetime as _datetime
    from decimal import Decimal as _Decimal
    from uuid import UUID as _UUID

    if isinstance(value, (_datetime, _date, _Decimal, _UUID)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    return value


# =============================================================================
# personal (INDIVIDUAL) projects — no company, owned by the caller
# =============================================================================
_PERSONAL_PROJECT_SELECT = """
    SELECT p.id, p.public_id, p.company_id, p.name, p.description, p.project_type,
           p.category, p.status, p.start_date, p.estimated_end_date, p.estimated_hours,
           p.estimated_budget, p.currency, p.billing_basis, p.billing_frequency,
           p.payment_terms_days, p.health_score, p.owner_user_id, p.metadata,
           p.created_at, p.updated_at,
           ou.public_id AS owner_public_id,
           NULLIF(TRIM(ou.first_name || ' ' || ou.last_name), '') AS owner_name,
           COALESCE(pr.role_count, 0)      AS role_count,
           COALESCE(pr.open_role_count, 0)  AS open_role_count,
           0 AS sow_count, 0 AS contract_count, 0 AS active_contract_count,
           0 AS invoiced_total, 0 AS outstanding_total,
           0 AS timesheet_count, 0 AS team_size,
           p.updated_at AS last_activity_at
      FROM public.projects p
      LEFT JOIN public.users ou ON ou.id = p.owner_user_id
      LEFT JOIN LATERAL (
            SELECT count(*) AS role_count,
                   count(*) FILTER (WHERE status = 'OPEN' AND allocated_count < required_count)
                       AS open_role_count
              FROM public.project_roles r WHERE r.project_id = p.id AND r.deleted_at IS NULL
      ) pr ON TRUE
"""


def _personal_from_row(row: Any) -> dict[str, Any]:
    data = _project_from_row(row, "")
    data["company_id"] = None
    return data


async def create_personal_project(
    conn: AsyncConnection,
    *,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Create an INDIVIDUAL project owned by the caller, in no company.

    Personal projects cannot name a company counterparty or a separate owner:
    there is no tenant to resolve them against.
    """
    if payload.get("counterparty_company_id"):
        raise ValidationError(
            "A personal project cannot name a company counterparty.",
            details={"reason": "PERSONAL_NO_COMPANY"},
        )
    if payload.get("owner_user_id"):
        raise ValidationError(
            "A personal project is always owned by its creator.",
            details={"reason": "PERSONAL_SELF_OWNED"},
        )

    row = (
        (
            await conn.execute(
                text(
                    """
                    INSERT INTO public.projects
                      (company_id, name, description, project_type, category, status,
                       start_date, estimated_end_date, estimated_hours, estimated_budget,
                       currency, billing_basis, billing_frequency, payment_terms_days,
                       owner_user_id, metadata, created_by)
                    VALUES
                      (NULL, :name, :description, :project_type, 'INDIVIDUAL', :status,
                       :start_date, :estimated_end_date, :estimated_hours, :estimated_budget,
                       :currency, :billing_basis, :billing_frequency, :payment_terms_days,
                       :owner, CAST(:metadata AS jsonb), :actor)
                    RETURNING public_id
                    """
                ),
                {
                    "name": payload["name"],
                    "description": payload.get("description"),
                    "project_type": payload.get("project_type", "SERVICE"),
                    "status": payload.get("status", "DRAFT"),
                    "start_date": payload.get("start_date"),
                    "estimated_end_date": payload.get("estimated_end_date"),
                    "estimated_hours": payload.get("estimated_hours"),
                    "estimated_budget": payload.get("estimated_budget"),
                    "currency": payload.get("currency", "USD"),
                    "billing_basis": payload.get("billing_basis", "TIMESHEET"),
                    "billing_frequency": payload.get("billing_frequency", "MONTHLY"),
                    "payment_terms_days": payload.get("payment_terms_days", 30),
                    "owner": actor_user_id,
                    "metadata": _json(payload.get("metadata") or {}),
                    "actor": actor_user_id,
                },
            )
        )
        .mappings()
        .first()
    )

    await audit.record(
        conn,
        action="project.created",
        resource_type="project",
        resource_public_id=str(row["public_id"]),
        actor_user_id=actor_user_id,
        new_values={"name": payload["name"], "category": "INDIVIDUAL"},
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_personal_project(conn, user_id=actor_user_id, public_id=str(row["public_id"]))


async def list_personal_projects(
    conn: AsyncConnection,
    *,
    user_id: uuid.UUID,
    search: str | None,
    status: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    where = ["p.company_id IS NULL", "p.owner_user_id = :uid", "p.deleted_at IS NULL"]
    params: dict[str, Any] = {"uid": user_id, "limit": limit + 1}
    if search:
        where.append("(p.name ILIKE :q OR p.description ILIKE :q)")
        params["q"] = f"%{search}%"
    if status:
        where.append("p.status = :status")
        params["status"] = status
    rows = (
        (
            await conn.execute(
                text(
                    f"""
                    {_PERSONAL_PROJECT_SELECT}
                     WHERE {" AND ".join(where)}
                     ORDER BY p.created_at DESC, p.public_id DESC
                     LIMIT :limit
                    """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    return [_personal_from_row(r) for r in rows]


async def get_personal_project(
    conn: AsyncConnection, *, user_id: uuid.UUID, public_id: str
) -> dict[str, Any]:
    row = (
        (
            await conn.execute(
                text(
                    f"""{_PERSONAL_PROJECT_SELECT}
                     WHERE p.public_id = :pid AND p.company_id IS NULL
                       AND p.owner_user_id = :uid AND p.deleted_at IS NULL"""
                ),
                {"pid": public_id, "uid": user_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ResourceNotFoundError("Project not found.")
    return _personal_from_row(row)


async def update_personal_project(
    conn: AsyncConnection,
    *,
    user_id: uuid.UUID,
    public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    changes: dict[str, Any],
) -> dict[str, Any]:
    before = await resolve_personal(conn, "projects", public_id, user_id, lock=True)

    current_status = str(before.get("status") or "")
    if current_status in {"COMPLETED", "CANCELLED", "CLOSED"}:
        raise InvalidStateTransitionError(
            f"A project in status {current_status} is read-only.",
            details={"status": current_status, "allowed": []},
        )

    updates = {k: v for k, v in changes.items() if k in _PROJECT_UPDATABLE and v is not None}
    updates.pop("owner_user_id", None)
    if "status" in updates and str(updates["status"]) != current_status:
        _assert_transition(PROJECT_TRANSITIONS, current_status, str(updates["status"]), "project")
    if "metadata" in updates:
        updates["metadata"] = _json(updates["metadata"])

    if updates:
        assignments = ", ".join(f"{col} = :{col}" for col in updates)
        await conn.execute(
            text(f"UPDATE public.projects SET {assignments} WHERE id = :rid"),  # noqa: S608
            {**updates, "rid": before["id"]},
        )

    after = await get_personal_project(conn, user_id=user_id, public_id=public_id)
    await audit.record(
        conn,
        action="project.updated",
        resource_type="project",
        resource_id=before["id"],
        resource_public_id=public_id,
        actor_user_id=actor_user_id,
        old_values={k: _jsonable(before.get(k)) for k in updates},
        new_values={k: _jsonable(after.get(k)) for k in updates},
        request_id=request_id,
        ip_address=ip_address,
    )
    return after


async def create_personal_project_role(
    conn: AsyncConnection,
    *,
    user_id: uuid.UUID,
    project_public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    project = await resolve_personal(conn, "projects", project_public_id, user_id)

    row = (
        (
            await conn.execute(
                text(
                    """
                    INSERT INTO public.project_roles
                      (project_id, company_id, title, description, required_count,
                       required_skills, seniority, min_hourly_rate, max_hourly_rate,
                       cost_rate, currency, billing_basis, allocation_pct,
                       start_date, end_date, status, created_by)
                    VALUES
                      (:pid, NULL, :title, :description, :required_count,
                       CAST(:skills AS text[]), :seniority, :min_rate, :max_rate,
                       :cost_rate, :currency, :billing_basis, :allocation,
                       :start_date, :end_date, :status, :actor)
                    RETURNING public_id, id
                    """
                ),
                {
                    "pid": project["id"],
                    "title": payload["title"],
                    "description": payload.get("description"),
                    "required_count": payload.get("required_count", 1),
                    "skills": _text_array(payload.get("required_skills") or []),
                    "seniority": payload.get("seniority"),
                    "min_rate": payload.get("min_hourly_rate"),
                    "max_rate": payload.get("max_hourly_rate"),
                    "cost_rate": payload.get("cost_rate"),
                    "currency": payload.get("currency", "USD"),
                    "billing_basis": payload.get("billing_basis", "TIMESHEET"),
                    "allocation": payload.get("allocation_pct", 100),
                    "start_date": payload.get("start_date"),
                    "end_date": payload.get("end_date"),
                    "status": payload.get("status", "OPEN"),
                    "actor": actor_user_id,
                },
            )
        )
        .mappings()
        .first()
    )

    await conn.execute(
        text("SELECT app.refresh_role_allocation(CAST(:rid AS uuid))"), {"rid": row["id"]}
    )

    await audit.record(
        conn,
        action="project_role.created",
        resource_type="project_role",
        resource_public_id=str(row["public_id"]),
        actor_user_id=actor_user_id,
        new_values={
            "project_id": project_public_id,
            "title": payload["title"],
            "required_count": payload.get("required_count", 1),
        },
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_personal_project_role(conn, user_id=user_id, public_id=str(row["public_id"]))


async def list_personal_project_roles(
    conn: AsyncConnection,
    *,
    user_id: uuid.UUID,
    project_public_id: str | None = None,
    limit: int,
) -> list[dict[str, Any]]:
    where = ["r.company_id IS NULL", "r.deleted_at IS NULL", "p.owner_user_id = :uid"]
    params: dict[str, Any] = {"uid": user_id, "limit": limit + 1}
    if project_public_id:
        project = await resolve_personal(conn, "projects", project_public_id, user_id)
        where.append("r.project_id = :pid")
        params["pid"] = project["id"]
    rows = (
        (
            await conn.execute(
                text(
                    f"""
                    {_ROLE_SELECT}
                     WHERE {" AND ".join(where)}
                     ORDER BY r.public_id ASC
                     LIMIT :limit
                    """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    return [_role_from_row(r) for r in rows]


async def get_personal_project_role(
    conn: AsyncConnection, *, user_id: uuid.UUID, public_id: str
) -> dict[str, Any]:
    row = (
        (
            await conn.execute(
                text(
                    f"""{_ROLE_SELECT}
                     WHERE r.public_id = :pid AND r.company_id IS NULL
                       AND r.deleted_at IS NULL
                       AND EXISTS (SELECT 1 FROM public.projects p
                                    WHERE p.id = r.project_id AND p.company_id IS NULL
                                      AND p.owner_user_id = :uid)"""  # noqa: S608
                ),
                {"pid": public_id, "uid": user_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ResourceNotFoundError("Project role not found.")
    return _role_from_row(row)


# =============================================================================
# personal (INDIVIDUAL) SOWs + acceptance with capture
# =============================================================================
async def _resolve_personal_role_ids(
    conn: AsyncConnection,
    *,
    user_id: uuid.UUID,
    project_id: uuid.UUID,
    requested: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Personal twin of `_resolve_project_role_ids`: only roles of this owned,
    company-less project are accepted."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in requested:
        role_public_id = item["project_role_id"]
        if role_public_id in seen:
            raise ValidationError(
                "The same project role appears twice.", details={"project_role_id": role_public_id}
            )
        seen.add(role_public_id)

        role = await resolve_personal(conn, "project_roles", role_public_id, user_id)
        if str(role["project_id"]) != str(project_id):
            raise BusinessRuleViolationError(
                "That role does not belong to this project.",
                details={
                    "project_role_id": role_public_id,
                    "reason": "PROJECT_ROLE_NOT_ON_PROJECT",
                },
            )
        out.append({**item, "project_role_id": role["id"]})
    return out


async def create_personal_sow(
    conn: AsyncConnection,
    *,
    user_id: uuid.UUID,
    project_public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Create a SOW under the caller's own personal project.

    The counterparty may be another user (INDIVIDUAL) or, when the caller also
    belongs to a company, that company — but the SOW itself stays personal.
    """
    project = await resolve_personal(conn, "projects", project_public_id, user_id)

    counterparty_company = None
    if payload.get("counterparty_company_id"):
        counterparty_company = await resolve_company_public_id(
            conn, payload["counterparty_company_id"]
        )
    counterparty_user = None
    if payload.get("counterparty_user_id"):
        counterparty_user = await resolve_user_public_id(conn, payload["counterparty_user_id"])
    if counterparty_company is None and counterparty_user is None:
        raise ValidationError(
            "A personal SOW names who the work is for: a counterparty user or company.",
            details={"reason": "COUNTERPARTY_REQUIRED"},
        )

    metadata = {
        "scope": payload.get("scope"),
        "deliverables": payload.get("deliverables") or [],
        "milestones": payload.get("milestones") or [],
    }

    row = (
        (
            await conn.execute(
                text(
                    """
                    INSERT INTO public.sows
                      (project_id, company_id, sow_type, counterparty_company_id,
                       counterparty_user_id, title, description, status, start_date, end_date,
                       currency, default_rate, billing_basis, billing_frequency,
                       invoice_frequency, payment_terms_days, payment_method,
                       special_conditions, max_total_amount, auto_generate_contracts,
                       document_id, metadata, created_by)
                    VALUES
                      (:pid, NULL, :sow_type, :counterparty_company,
                       :counterparty_user, :title, :description, 'DRAFT', :start_date, :end_date,
                       :currency, :default_rate, :billing_basis, :billing_frequency,
                       :invoice_frequency, :payment_terms_days, :payment_method,
                       :special_conditions, :max_total_amount, :auto_generate,
                       :document_id, CAST(:metadata AS jsonb), :actor)
                    RETURNING public_id
                    """
                ),
                {
                    "pid": project["id"],
                    "sow_type": payload.get("sow_type", "INDIVIDUAL"),
                    "counterparty_company": counterparty_company,
                    "counterparty_user": counterparty_user,
                    "title": payload["title"],
                    "description": payload.get("description"),
                    "start_date": payload.get("start_date"),
                    "end_date": payload.get("end_date"),
                    "currency": payload.get("currency", "USD"),
                    "default_rate": payload.get("default_rate"),
                    "billing_basis": payload.get("billing_basis", "TIMESHEET"),
                    "billing_frequency": payload.get("billing_frequency", "MONTHLY"),
                    "invoice_frequency": payload.get("invoice_frequency", "MONTHLY"),
                    "payment_terms_days": payload.get("payment_terms_days", 30),
                    "payment_method": payload.get("payment_method"),
                    "special_conditions": payload.get("special_conditions"),
                    "max_total_amount": payload.get("max_total_amount"),
                    "auto_generate": payload.get("auto_generate_contracts", True),
                    "document_id": None,
                    "metadata": _json(metadata),
                    "actor": actor_user_id,
                },
            )
        )
        .mappings()
        .first()
    )

    created = await resolve_personal(conn, "sows", str(row["public_id"]), user_id, columns="id")
    await _replace_sow_roles(
        conn,
        sow_id=uuid.UUID(str(created["id"])),
        requested=await _resolve_personal_role_ids(
            conn,
            user_id=user_id,
            project_id=uuid.UUID(str(project["id"])),
            requested=payload.get("roles") or [],
        ),
    )

    await audit.record(
        conn,
        action="sow.created",
        resource_type="sow",
        resource_public_id=str(row["public_id"]),
        actor_user_id=actor_user_id,
        new_values={"project_id": project_public_id, "title": payload["title"]},
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_personal_sow(conn, user_id=user_id, public_id=str(row["public_id"]))


async def list_personal_sows(
    conn: AsyncConnection,
    *,
    user_id: uuid.UUID,
    project_public_id: str | None,
    status: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    where = ["s.company_id IS NULL", "s.created_by = :uid", "s.deleted_at IS NULL"]
    params: dict[str, Any] = {"uid": user_id, "limit": limit + 1}
    if project_public_id:
        project = await resolve_personal(conn, "projects", project_public_id, user_id)
        where.append("s.project_id = :pid")
        params["pid"] = project["id"]
    if status:
        where.append("s.status = :status")
        params["status"] = status
    rows = (
        (
            await conn.execute(
                text(
                    f"""
                    {_SOW_SELECT}
                     WHERE {" AND ".join(where)}
                     ORDER BY s.updated_at DESC, s.public_id DESC
                     LIMIT :limit
                    """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    out = []
    for r in rows:
        data = _sow_from_row(r)
        data["roles"] = await _sow_roles(conn, r["id"])
        out.append(data)
    return out


async def get_personal_sow(
    conn: AsyncConnection, *, user_id: uuid.UUID, public_id: str, with_history: bool = False
) -> dict[str, Any]:
    row = (
        (
            await conn.execute(
                text(
                    f"""{_SOW_SELECT}
                     WHERE s.public_id = :pid AND s.company_id IS NULL
                       AND s.created_by = :uid AND s.deleted_at IS NULL"""
                ),
                {"pid": public_id, "uid": user_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ResourceNotFoundError("SOW not found.")
    data = _sow_from_row(row)
    data["roles"] = await _sow_roles(conn, row["id"])
    data["contract_count"] = int(row["contract_count"] or 0)
    data["contract_ids"] = list(row["contract_ids"] or [])
    if with_history:
        data["history"] = await _sow_history(conn, public_id, None)
    return data


async def submit_personal_sow(
    conn: AsyncConnection,
    *,
    user_id: uuid.UUID,
    public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
) -> dict[str, Any]:
    """DRAFT → PENDING_APPROVAL for a personal SOW, so its counterparty can decide."""
    before = await resolve_personal(conn, "sows", public_id, user_id, lock=True)
    current = str(before["status"])
    if current != "DRAFT":
        raise InvalidStateTransitionError(
            f"A personal SOW in status {current} cannot be submitted.",
            details={"status": current, "acceptable_in": ["DRAFT"]},
        )
    await conn.execute(
        text("UPDATE public.sows SET status = 'PENDING_APPROVAL' WHERE id = :rid"),
        {"rid": before["id"]},
    )
    await audit.record(
        conn,
        action="sow.submitted",
        resource_type="sow",
        resource_id=before["id"],
        resource_public_id=public_id,
        actor_user_id=actor_user_id,
        old_values={"status": current},
        new_values={"status": "PENDING_APPROVAL"},
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_personal_sow(conn, user_id=user_id, public_id=public_id)


async def _resolve_sow_for_decision(
    conn: AsyncConnection,
    *,
    public_id: str,
    user_id: uuid.UUID,
    company_id: uuid.UUID | None,
) -> dict[str, Any]:
    """Fetch a SOW the caller may accept or reject: owner, counterparty member,
    counterparty user, or personal owner. Anything else is a 404."""
    row = (
        (
            await conn.execute(
                text(
                    """
                    SELECT s.id, s.public_id, s.status, s.company_id,
                           s.counterparty_company_id, s.counterparty_user_id,
                           s.created_by, s.rejected_by, s.rejected_at, s.reject_reason
                      FROM public.sows s
                     WHERE s.public_id = :pid AND s.deleted_at IS NULL
                    """
                ),
                {"pid": public_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ResourceNotFoundError("SOW not found.")
    data = dict(row)

    allowed = False
    if data["company_id"] is None:
        allowed = str(data["created_by"]) == str(user_id) or str(
            data["counterparty_user_id"] or ""
        ) == str(user_id)
    elif company_id is not None and str(data["company_id"]) == str(company_id):
        allowed = True
    elif data["counterparty_company_id"] is not None:
        member = await conn.execute(
            text("SELECT app.is_member(CAST(:cid AS uuid))"),
            {"cid": data["counterparty_company_id"]},
        )
        allowed = bool(member.scalar())
    elif data["counterparty_user_id"] is not None:
        allowed = str(data["counterparty_user_id"]) == str(user_id)
    if not allowed:
        raise ResourceNotFoundError("SOW not found.")
    return data


async def accept_sow(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID | None,
    user_id: uuid.UUID,
    public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
) -> dict[str, Any]:
    """Accept a SOW as its counterparty. Captures who accepted and when."""
    before = await _resolve_sow_for_decision(
        conn, public_id=public_id, user_id=user_id, company_id=company_id
    )
    current = str(before["status"])
    if current != "PENDING_APPROVAL":
        raise InvalidStateTransitionError(
            f"A SOW in status {current} cannot be accepted.",
            details={"status": current, "acceptable_in": ["PENDING_APPROVAL"]},
        )
    await conn.execute(
        text(
            "UPDATE public.sows SET status = 'ACTIVE', approved_by = :actor,"
            " approved_at = now() WHERE id = :rid"
        ),
        {"actor": actor_user_id, "rid": before["id"]},
    )
    await audit.record(
        conn,
        action="sow.accepted",
        resource_type="sow",
        resource_id=before["id"],
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        old_values={"status": current},
        new_values={"status": "ACTIVE"},
        request_id=request_id,
        ip_address=ip_address,
    )
    if before["company_id"] is None:
        # §17: an accepted individual SOW automatically gains its engagement
        # contract, so it can never be an orphan that cannot become executable.
        # Local import: contracts.py already imports from this module.
        role_count = (
            await conn.execute(
                text("SELECT count(*) FROM public.sow_roles WHERE sow_id = :sid"),
                {"sid": before["id"]},
            )
        ).scalar()
        if int(role_count or 0) > 0:
            from app.services import contracts as contract_service

            await contract_service.create_personal_contract_from_sow(
                conn,
                sow_public_id=public_id,
                owner_user_id=uuid.UUID(str(before["created_by"])),
                request_id=request_id,
            )
    return await _get_sow_after_decision(conn, public_id=public_id)


async def reject_sow(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID | None,
    user_id: uuid.UUID,
    public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    reason: str,
    notes: str | None = None,
) -> dict[str, Any]:
    """Reject a SOW. The row is kept with rejected-by/at/reason — never deleted."""
    if not (reason or "").strip():
        raise ValidationError(
            "A reason is required to reject a SOW.", details={"reason": "REASON_REQUIRED"}
        )
    before = await _resolve_sow_for_decision(
        conn, public_id=public_id, user_id=user_id, company_id=company_id
    )
    current = str(before["status"])
    if current != "PENDING_APPROVAL":
        raise InvalidStateTransitionError(
            f"A SOW in status {current} cannot be rejected.",
            details={"status": current, "rejectable_in": ["PENDING_APPROVAL"]},
        )
    await conn.execute(
        text(
            "UPDATE public.sows SET status = 'REJECTED', rejected_by = :actor,"
            " rejected_at = now(), reject_reason = :reason WHERE id = :rid"
        ),
        {"actor": actor_user_id, "reason": reason.strip(), "rid": before["id"]},
    )
    await audit.record(
        conn,
        action="sow.rejected",
        resource_type="sow",
        resource_id=before["id"],
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        old_values={"status": current},
        new_values={"status": "REJECTED", "reason": reason.strip(), "notes": notes},
        reason=reason.strip(),
        request_id=request_id,
        ip_address=ip_address,
    )
    return await _get_sow_after_decision(conn, public_id=public_id)


async def _get_sow_after_decision(conn: AsyncConnection, *, public_id: str) -> dict[str, Any]:
    row = (
        (
            await conn.execute(
                text(f"{_SOW_SELECT} WHERE s.public_id = :pid"),
                {"pid": public_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:  # pragma: no cover - the row was just updated
        raise ResourceNotFoundError("SOW not found.")
    data = _sow_from_row(row)
    data["roles"] = await _sow_roles(conn, row["id"])
    return data
