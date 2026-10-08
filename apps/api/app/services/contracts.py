"""Contract lifecycle: draft, internal review, approval, send, accept, activate,
renew and terminate, plus the contract roles that carry the commercial terms.

The contract role is the authoritative source for timesheet billing. This module
therefore refuses to create a contract role that does not point at a project role
on the same project, and it snapshots the agreed rate onto `contracts.metadata`
so a later invoice can prove which terms were in force.
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
from app.services.code import CONTRACT_TRANSITIONS, _json, _jsonable
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


# =============================================================================
# reads
# =============================================================================
_CONTRACT_SELECT = """
    SELECT c.id, c.public_id, c.project_id, c.sow_id, c.company_id, c.title,
           c.contract_type, c.status, c.currency, c.billing_basis, c.billing_frequency,
           c.payment_terms_days, c.start_date, c.end_date, c.auto_renew,
           c.renewal_notice_days, c.termination_notice_days, c.notice_period_end,
           c.governing_law, c.confidentiality_level, c.requires_timesheets, c.locked,
           c.counterparty_company_id, c.counterparty_user_id, c.document_id,
           c.risk_score, c.version, c.sent_at, c.responded_at, c.activated_at,
           c.terminated_at, c.response_notes, c.created_at, c.updated_at,
           ru.public_id AS responded_by_public_id,
           c.metadata,
           p.public_id AS project_public_id, p.name AS project_name,
           s.public_id AS sow_public_id, s.title AS sow_title,
           cp.public_id AS counterparty_company_public_id,
           COALESCE(cp.display_name, cp.legal_name) AS counterparty_company_name,
           cu.public_id AS counterparty_user_public_id,
           NULLIF(TRIM(cu.first_name || ' ' || cu.last_name), '') AS counterparty_user_name,
           COALESCE(iv.invoiced_total, 0)  AS invoiced_total,
           COALESCE(iv.outstanding_total, 0) AS outstanding_total,
           COALESCE(iv.invoice_count, 0)   AS invoice_count,
           COALESCE(asg.assignment_count, 0) AS assignment_count,
           COALESCE(ts.timesheet_count, 0)  AS timesheet_count
      FROM public.contracts c
      JOIN public.projects p ON p.id = c.project_id
      JOIN public.sows s      ON s.id = c.sow_id
      LEFT JOIN public.companies cp ON cp.id = c.counterparty_company_id
      LEFT JOIN public.users cu     ON cu.id = c.counterparty_user_id
      LEFT JOIN public.users ru     ON ru.id = c.responded_by
      LEFT JOIN LATERAL (
            SELECT sum(i.total_amount) FILTER (
                     WHERE i.status NOT IN ('DRAFT','PENDING','SUBMITTED','CANCELLED','REJECTED')
                   ) AS invoiced_total,
                   sum(i.balance_due) FILTER (
                     WHERE i.status NOT IN ('CANCELLED','REJECTED','REFUNDED')
                   ) AS outstanding_total,
                   count(*) AS invoice_count
              FROM public.invoices i
             WHERE i.contract_id = c.id AND i.direction = 'RECEIVABLE' AND i.deleted_at IS NULL
      ) iv ON TRUE
      LEFT JOIN LATERAL (
            SELECT count(*) AS assignment_count FROM public.assignments a
             WHERE a.contract_id = c.id
      ) asg ON TRUE
      LEFT JOIN LATERAL (
            SELECT count(*) AS timesheet_count FROM public.timesheets t
             WHERE t.contract_id = c.id AND t.status IN ('APPROVED','LOCKED')
      ) ts ON TRUE
"""


def _contract_from_row(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["project_id"] = data.pop("project_public_id")
    data["sow_id"] = data.pop("sow_public_id")
    data["counterparty_company_id"] = data.pop("counterparty_company_public_id", None)
    data["counterparty_user_id"] = data.pop("counterparty_user_public_id", None)
    data["responded_by"] = data.pop("responded_by_public_id", None)
    data["project_name"] = data.get("project_name")
    data["sow_title"] = data.get("sow_title")
    data["counterparty_company_name"] = data.get("counterparty_company_name")
    data["counterparty_user_name"] = data.get("counterparty_user_name")
    meta = json_or_empty(data.pop("metadata", None))
    data["contract_value"] = meta.get("contract_value")
    return data


async def _contract_children(
    conn: AsyncConnection, contract_id: uuid.UUID
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    roles = (
        (
            await conn.execute(
                text(
                    """
                    SELECT pr.public_id AS project_role_id, pr.title AS role_title,
                           cr.quantity, cr.rate, cr.rate_type, cr.currency,
                           cr.billing_basis, cr.billing_frequency, cr.payment_terms_days,
                           cr.max_units, cr.start_date, cr.end_date, cr.overtime_rule,
                           cr.overtime_rate_multiplier, cr.tax_rule, cr.tax_rate, cr.notes,
                           COALESCE(billed.units, 0) AS billed_units
                      FROM public.contract_roles cr
                      JOIN public.project_roles pr ON pr.id = cr.project_role_id
                      LEFT JOIN LATERAL (
                            SELECT sum(t.billable_hours) AS units
                              FROM public.timesheets t
                             WHERE t.contract_role_id = cr.id
                               AND t.status IN ('APPROVED','LOCKED')
                      ) billed ON TRUE
                     WHERE cr.contract_id = :cid
                     ORDER BY pr.public_id
                    """
                ),
                {"cid": contract_id},
            )
        )
        .mappings()
        .all()
    )

    parties = (
        (
            await conn.execute(
                text(
                    """
                    SELECT cp.public_id AS company_id,
                           COALESCE(cp.display_name, cp.legal_name) AS company_name,
                           cu.public_id AS user_id,
                           NULLIF(TRIM(cu.first_name || ' ' || cu.last_name), '') AS user_name,
                           p.party_role, p.signatory_name, p.signatory_email, p.signed_at
                      FROM public.contract_parties p
                      LEFT JOIN public.companies cp ON cp.id = p.party_company_id
                      LEFT JOIN public.users cu     ON cu.id = p.party_user_id
                     WHERE p.contract_id = :cid
                     ORDER BY p.party_role, p.id
                    """
                ),
                {"cid": contract_id},
            )
        )
        .mappings()
        .all()
    )

    line_items = (
        (
            await conn.execute(
                text(
                    """
                    SELECT li.id::text AS id, li.label, li.description, li.line_type,
                           li.quantity, li.unit, li.unit_rate, li.amount, li.currency,
                           li.billing_basis, li.billing_frequency, li.tax_rate,
                           li.is_taxable, li.is_additional, li.third_party_name,
                           li.cap_amount, li.sort_order, li.is_active,
                           pr.public_id AS project_role_id
                      FROM public.contract_line_items li
                      LEFT JOIN public.contract_roles cr   ON cr.id = li.contract_role_id
                      LEFT JOIN public.project_roles pr    ON pr.id = cr.project_role_id
                     WHERE li.contract_id = :cid
                     ORDER BY li.sort_order, li.id
                    """
                ),
                {"cid": contract_id},
            )
        )
        .mappings()
        .all()
    )

    steps = (
        (
            await conn.execute(
                text(
                    """
                    SELECT step_no, name, status, required_permission,
                           approver_user_id::text AS approver_user_id,
                           approver_company_id::text AS approver_company_id,
                           decided_at, notes
                      FROM public.contract_approval_steps
                     WHERE contract_id = :cid
                     ORDER BY step_no
                    """
                ),
                {"cid": contract_id},
            )
        )
        .mappings()
        .all()
    )

    steps_out = []
    for s in steps:
        entry = dict(s)
        if entry.get("approver_user_id"):
            entry["approver_user_id"] = await _user_public(
                conn, uuid.UUID(str(entry["approver_user_id"]))
            )
        if entry.get("approver_company_id"):
            entry["approver_company_id"] = await _company_public(
                conn, uuid.UUID(str(entry["approver_company_id"]))
            )
        steps_out.append(entry)

    return (
        [dict(r) for r in roles],
        [dict(r) for r in parties],
        [dict(r) for r in line_items],
        steps_out,
    )


async def _user_public(conn: AsyncConnection, user_id: uuid.UUID) -> str | None:
    row = (
        (
            await conn.execute(
                text("SELECT public_id FROM public.users WHERE id = CAST(:uid AS uuid)"),
                {"uid": user_id},
            )
        )
        .mappings()
        .first()
    )
    return str(row["public_id"]) if row else None


async def _company_public(conn: AsyncConnection, company_id: uuid.UUID) -> str | None:
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
    return str(row["public_id"]) if row else None


# Order matters: commercial terms are reviewed by whoever owns contracts, then by
# whoever owns money. Each step names the permission that satisfies it, so the
# approver check and the audit entry read the same rule.
CONTRACT_APPROVAL_TEMPLATES: tuple[tuple[str, str, int], ...] = (
    ("Commercial review", "contracts.approve", 3),
    ("Financial review", "invoices.approve", 3),
)


async def _build_contract_approval_chain(
    conn: AsyncConnection, *, contract_id: uuid.UUID, company_id: uuid.UUID
) -> None:
    """Create the pending internal-approval steps for a contract.

    Approvers are not named up front: a step is satisfied by any member holding
    the required permission, which keeps the chain working when someone is off.
    """
    existing = await conn.execute(
        text("SELECT count(*) FROM public.contract_approval_steps WHERE contract_id = :cid"),
        {"cid": contract_id},
    )
    if as_decimal(existing.scalar()) > 0:
        return

    for step_no, (name, permission, due_days) in enumerate(CONTRACT_APPROVAL_TEMPLATES, start=1):
        await conn.execute(
            text(
                """
                INSERT INTO public.contract_approval_steps
                  (contract_id, step_no, name, approver_company_id,
                   required_permission, status, due_at)
                VALUES (:cid, :step, :name, :company, :permission, 'PENDING', :due)
                """
            ),
            {
                "cid": contract_id,
                "step": step_no,
                "name": name,
                "company": company_id,
                "permission": permission,
                "due": utc_today() + timedelta(days=due_days),
            },
        )


async def get_contract(
    conn: AsyncConnection, *, company_id: uuid.UUID, public_id: str
) -> dict[str, Any]:
    row = (
        (
            await conn.execute(
                text(f"{_CONTRACT_SELECT} WHERE c.public_id = :pid AND c.company_id = :cid"),
                {"pid": public_id, "cid": company_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ResourceNotFoundError("Contract not found.")

    roles, parties, line_items, steps = await _contract_children(conn, row["id"])
    data = _contract_from_row(row)
    data["roles"] = roles
    data["parties"] = parties
    data["line_items"] = line_items
    data["approval_steps"] = steps
    data["allowed_transitions"] = list(CONTRACT_TRANSITIONS.get(str(row["status"]), ()))
    return data


async def list_contracts(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    project_public_id: str | None = None,
    sow_public_id: str | None = None,
    status: str | None = None,
    search: str | None = None,
    expiring_within_days: int | None = None,
    limit: int,
    cursor_keys: dict[str, str],
) -> list[dict[str, Any]]:
    where = ["c.company_id = :cid", "c.deleted_at IS NULL"]
    params: dict[str, Any] = {"cid": company_id, "limit": limit + 1}

    if project_public_id:
        project = await resolve_scoped(conn, "projects", project_public_id, company_id)
        where.append("c.project_id = :pid")
        params["pid"] = project["id"]
    if sow_public_id:
        sow = await resolve_scoped(conn, "sows", sow_public_id, company_id)
        where.append("c.sow_id = :sid")
        params["sid"] = sow["id"]
    if status:
        where.append("c.status = :status")
        params["status"] = status
    if search:
        where.append("c.title ILIKE :q")
        params["q"] = f"%{search}%"
    if expiring_within_days:
        where.append(
            "c.end_date IS NOT NULL AND c.end_date <= current_date + :window"
            " AND c.status IN ('ACCEPTED','ACTIVE')"
        )
        params["window"] = expiring_within_days
    if cursor_keys.get("updated_at"):
        where.append("(c.updated_at, c.public_id) < (:cur_updated, :cur_public)")
        params["cur_updated"] = cursor_keys["updated_at"]
        params["cur_public"] = cursor_keys["public_id"]

    rows = (
        (
            await conn.execute(
                text(
                    f"""
                    {_CONTRACT_SELECT}
                     WHERE {" AND ".join(where)}
                     ORDER BY c.updated_at DESC, c.public_id DESC
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
        data = _contract_from_row(r)
        data["roles"] = []
        data["parties"] = []
        data["line_items"] = []
        data["approval_steps"] = []
        data["allowed_transitions"] = list(CONTRACT_TRANSITIONS.get(str(r["status"]), ()))
        out.append(data)
    return out


# =============================================================================
# create
# =============================================================================
async def create_contract(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    project = await resolve_scoped(conn, "projects", payload["project_id"], company_id)
    sow = await resolve_scoped(conn, "sows", payload["sow_id"], company_id)

    # A contract must reference a SOW on the same project. The database guard
    # `app.assert_contract_insert` also enforces this; checking here turns the
    # failure into a precise 409 instead of a raw check violation.
    if str(sow["project_id"]) != str(project["id"]):
        raise BusinessRuleViolationError(
            "The selected SOW belongs to a different project.",
            details={"reason": "SOW_PROJECT_MISMATCH"},
        )

    counterparty_company = (
        await resolve_company_public_id(conn, payload["counterparty_company_id"])
        if payload.get("counterparty_company_id")
        else sow.get("counterparty_company_id")
    )
    counterparty_user = (
        await resolve_user_public_id(conn, payload["counterparty_user_id"])
        if payload.get("counterparty_user_id")
        else sow.get("counterparty_user_id")
    )
    document_id = None
    if payload.get("document_id"):
        doc = await resolve_scoped(conn, "documents", payload["document_id"], company_id)
        document_id = doc["id"]

    roles = await _resolve_contract_roles(
        conn,
        company_id=company_id,
        project_id=project["id"],
        requested=payload.get("roles") or [],
    )

    start_date = payload.get("start_date") or sow.get("start_date")
    end_date = payload.get("end_date") or sow.get("end_date")
    contract_value = payload.get("contract_value")
    if contract_value is None:
        contract_value = sow.get("max_total_amount")

    from app.services.billing import money

    metadata = {
        # Decimal-safe: JSON cannot carry Decimal, so persist the exact string.
        "contract_value": str(money(contract_value)) if contract_value is not None else None,
        "terms_snapshot": payload.get("terms_snapshot") or {},
        "renewal": {
            "auto_renew": payload.get("auto_renew", False),
            "renewal_notice_days": payload.get("renewal_notice_days"),
        },
    }

    row = (
        (
            await conn.execute(
                text(
                    """
                    INSERT INTO public.contracts
                      (sow_id, project_id, company_id, contract_type, title, status,
                       currency, billing_basis, billing_frequency, payment_terms_days,
                       start_date, end_date, auto_renew, renewal_notice_days,
                       termination_notice_days, governing_law, confidentiality_level,
                       requires_timesheets, counterparty_company_id, counterparty_user_id,
                       document_id, metadata, created_by)
                    VALUES
                      (:sow, :pid, :cid, :ctype, :title, 'DRAFT',
                       :currency, :basis, :frequency, :terms,
                       :start_date, :end_date, :auto_renew, :renewal_notice,
                       :termination_notice, :law, :confidentiality,
                       :requires_timesheets, :counterparty_company, :counterparty_user,
                       :document_id, CAST(:metadata AS jsonb), :actor)
                    RETURNING public_id
                    """
                ),
                {
                    "sow": sow["id"],
                    "pid": project["id"],
                    "cid": company_id,
                    "ctype": payload.get("contract_type", "COMPANY"),
                    "title": payload["title"],
                    "currency": payload.get("currency", "USD"),
                    "basis": payload.get("billing_basis", "TIMESHEET"),
                    "frequency": payload.get("billing_frequency", "MONTHLY"),
                    "terms": payload.get("payment_terms_days", 30),
                    "start_date": start_date,
                    "end_date": end_date,
                    "auto_renew": payload.get("auto_renew", False),
                    "renewal_notice": payload.get("renewal_notice_days"),
                    "termination_notice": payload.get("termination_notice_days"),
                    "law": payload.get("governing_law"),
                    "confidentiality": payload.get("confidentiality_level", "STANDARD"),
                    "requires_timesheets": payload.get("requires_timesheets", True),
                    "counterparty_company": counterparty_company,
                    "counterparty_user": counterparty_user,
                    "document_id": document_id,
                    "metadata": _json(metadata),
                    "actor": actor_user_id,
                },
            )
        )
        .mappings()
        .first()
    )

    resolved = await resolve_scoped(
        conn, "contracts", str(row["public_id"]), company_id, columns="id"
    )
    contract_id = uuid.UUID(str(resolved["id"]))

    await _insert_contract_roles(
        conn,
        contract_id=contract_id,
        company_id=company_id,
        requested=roles,
        default_currency=payload.get("currency", "USD"),
        default_basis=payload.get("billing_basis", "TIMESHEET"),
        default_frequency=payload.get("billing_frequency", "MONTHLY"),
        default_terms=payload.get("payment_terms_days", 30),
        start_date=start_date,
        end_date=end_date,
    )
    await _insert_contract_parties(
        conn,
        contract_id=contract_id,
        company_id=company_id,
        payload=payload,
        counterparty_company=counterparty_company,
        counterparty_user=counterparty_user,
        owner_company=company_id,
    )
    await _insert_contract_line_items(
        conn, contract_id=contract_id, requested=payload.get("line_items") or []
    )

    await audit.record(
        conn,
        action="contract.created",
        resource_type="contract",
        resource_public_id=str(row["public_id"]),
        company_id=company_id,
        actor_user_id=actor_user_id,
        new_values={
            "project_id": payload["project_id"],
            "sow_id": payload["sow_id"],
            "title": payload["title"],
            "role_count": len(roles),
            "contract_value": _jsonable(contract_value),
        },
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_contract(conn, company_id=company_id, public_id=str(row["public_id"]))


async def _resolve_contract_roles(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    project_id: uuid.UUID,
    requested: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Resolve and validate contract roles against the project's real roles."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in requested:
        role_public_id = item["project_role_id"]
        if role_public_id in seen:
            raise ValidationError(
                "The same project role appears twice on this contract.",
                details={"project_role_id": role_public_id},
            )
        seen.add(role_public_id)

        role = await resolve_scoped(conn, "project_roles", role_public_id, company_id)
        if str(role["project_id"]) != str(project_id):
            raise BusinessRuleViolationError(
                "That role does not belong to this contract's project.",
                details={
                    "project_role_id": role_public_id,
                    "reason": "PROJECT_ROLE_NOT_ON_PROJECT",
                },
            )
        out.append({**item, "project_role_id": role["id"]})
    return out


async def _insert_contract_roles(
    conn: AsyncConnection,
    *,
    contract_id: uuid.UUID,
    company_id: uuid.UUID,
    requested: Sequence[dict[str, Any]],
    default_currency: str,
    default_basis: str,
    default_frequency: str,
    default_terms: int,
    start_date: date | None,
    end_date: date | None,
) -> None:
    for item in requested:
        await conn.execute(
            text(
                """
                INSERT INTO public.contract_roles
                  (contract_id, project_role_id, quantity, rate, rate_type, currency,
                   billing_basis, billing_frequency, payment_terms_days, max_units,
                   start_date, end_date, overtime_rule, overtime_rate_multiplier,
                   tax_rule, tax_rate, notes)
                VALUES
                  (:cid, CAST(:prid AS uuid), :quantity, :rate, :rate_type, :currency,
                   :basis, :frequency, :terms, :max_units,
                   :start_date, :end_date, :overtime_rule, :overtime_multiplier,
                   :tax_rule, :tax_rate, :notes)
                """
            ),
            {
                "cid": contract_id,
                "prid": item["project_role_id"],
                "quantity": item.get("quantity", 1),
                "rate": item.get("rate"),
                "rate_type": item.get("rate_type", "HOURLY"),
                "currency": item.get("currency", default_currency),
                "basis": item.get("billing_basis") or default_basis,
                "frequency": item.get("billing_frequency") or default_frequency,
                "terms": item.get("payment_terms_days", default_terms),
                "max_units": item.get("max_units"),
                "start_date": item.get("start_date") or start_date,
                "end_date": item.get("end_date") or end_date,
                "overtime_rule": item.get("overtime_rule", "NONE"),
                "overtime_multiplier": item.get("overtime_rate_multiplier", 1),
                "tax_rule": item.get("tax_rule", "STANDARD"),
                "tax_rate": item.get("tax_rate", 0),
                "notes": item.get("notes"),
            },
        )


async def _insert_contract_parties(
    conn: AsyncConnection,
    *,
    contract_id: uuid.UUID,
    company_id: uuid.UUID,
    payload: dict[str, Any],
    counterparty_company: Any,
    counterparty_user: Any,
    owner_company: uuid.UUID,
) -> None:
    requested = payload.get("parties") or []
    if not requested:
        # Default: our company plus the counterparty, as PRIMARY and COUNTERPARTY.
        await conn.execute(
            text(
                """
                INSERT INTO public.contract_parties
                  (contract_id, party_company_id, party_role)
                VALUES
                  (:cid, :owner, 'PRIMARY'),
                  (:cid, :counterparty, 'COUNTERPARTY')
                """
            ),
            {"cid": contract_id, "owner": owner_company, "counterparty": counterparty_company},
        )
        return

    for party in requested:
        await conn.execute(
            text(
                """
                INSERT INTO public.contract_parties
                  (contract_id, party_company_id, party_user_id, party_role,
                   signatory_name, signatory_email)
                VALUES (:cid, :company, :user, :role, :signatory_name, signatory_email)
                """
            ),
            {
                "cid": contract_id,
                "company": party.get("party_company_id"),
                "user": party.get("party_user_id"),
                "role": party.get("party_role", "PRIMARY"),
                "signatory_name": party.get("signatory_name"),
                "signatory_email": party.get("signatory_email"),
            },
        )


async def _insert_contract_line_items(
    conn: AsyncConnection, *, contract_id: uuid.UUID, requested: Sequence[dict[str, Any]]
) -> None:
    for item in requested:
        await conn.execute(
            text(
                """
                INSERT INTO public.contract_line_items
                  (contract_id, contract_role_id, line_type, label, description,
                   quantity, unit, unit_rate, currency, billing_basis, billing_frequency,
                   tax_rate, is_taxable, is_additional, third_party_name,
                   proration_start, proration_end, cap_amount, sort_order)
                VALUES
                  (:cid, CAST(:crid AS uuid), :line_type, :label, :description,
                   :quantity, :unit, :unit_rate, :currency, :basis, :frequency,
                   :tax_rate, :is_taxable, :is_additional, :third_party,
                   :proration_start, :proration_end, :cap_amount, :sort_order)
                """
            ),
            {
                "cid": contract_id,
                "crid": item.get("contract_role_id"),
                "line_type": item.get("line_type", "FIXED"),
                "label": item["label"],
                "description": item.get("description"),
                "quantity": item.get("quantity", 1),
                "unit": item.get("unit", "HOUR"),
                "unit_rate": item.get("unit_rate", 0),
                "currency": item.get("currency", "USD"),
                "basis": item.get("billing_basis", "FIXED"),
                "frequency": item.get("billing_frequency", "MONTHLY"),
                "tax_rate": item.get("tax_rate", 0),
                "is_taxable": item.get("is_taxable", False),
                "is_additional": item.get("is_additional", False),
                "third_party": item.get("third_party_name"),
                "proration_start": item.get("proration_start"),
                "proration_end": item.get("proration_end"),
                "cap_amount": item.get("cap_amount"),
                "sort_order": item.get("sort_order", 0),
            },
        )


# =============================================================================
# update / versions
# =============================================================================
_CONTRACT_UPDATABLE = frozenset(
    {
        "title",
        "currency",
        "billing_basis",
        "billing_frequency",
        "payment_terms_days",
        "start_date",
        "end_date",
        "auto_renew",
        "renewal_notice_days",
        "termination_notice_days",
        "governing_law",
        "confidentiality_level",
        "requires_timesheets",
        "document_id",
    }
)


async def update_contract(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    changes: dict[str, Any],
) -> dict[str, Any]:
    before = await resolve_scoped(conn, "contracts", public_id, company_id, lock=True)
    if str(before["status"]) != "DRAFT" or before["locked"]:
        raise InvalidStateTransitionError(
            "Commercial terms can only be edited while the contract is a draft.",
            details={"status": before["status"], "locked": bool(before["locked"])},
        )

    updates = {k: v for k, v in changes.items() if k in _CONTRACT_UPDATABLE and v is not None}
    if "document_id" in updates:
        doc = await resolve_scoped(conn, "documents", str(updates["document_id"]), company_id)
        updates["document_id"] = doc["id"]

    metadata = json_or_empty(before.get("metadata"))
    new_metadata = dict(metadata)
    if changes.get("contract_value") is not None:
        from app.services.billing import money

        new_metadata["contract_value"] = str(money(changes["contract_value"]))

    if updates:
        assignments = ", ".join(f"{col} = :{col}" for col in updates)
        await conn.execute(
            text(f"UPDATE public.contracts SET {assignments} WHERE id = :rid"),  # noqa: S608
            {**updates, "rid": before["id"]},
        )

    await conn.execute(
        text("UPDATE public.contracts SET metadata = CAST(:meta AS jsonb) WHERE id = :rid"),
        {"meta": _json(new_metadata), "rid": before["id"]},
    )

    if changes.get("roles") is not None:
        resolved = await _resolve_contract_roles(
            conn,
            company_id=company_id,
            project_id=before["project_id"],
            requested=changes["roles"],
        )
        await _clear_contract_roles(conn, contract_id=before["id"], project_id=before["project_id"])
        await _insert_contract_roles(
            conn,
            contract_id=before["id"],
            company_id=company_id,
            requested=resolved,
            default_currency=updates.get("currency") or before["currency"],
            default_basis=updates.get("billing_basis") or before["billing_basis"],
            default_frequency=updates.get("billing_frequency") or before["billing_frequency"],
            default_terms=updates.get("payment_terms_days") or int(before["payment_terms_days"]),
            start_date=updates.get("start_date") or before["start_date"],
            end_date=updates.get("end_date") or before["end_date"],
        )

    if changes.get("line_items") is not None:
        await conn.execute(
            text("DELETE FROM public.contract_line_items WHERE contract_id = :cid"),
            {"cid": before["id"]},
        )
        await _insert_contract_line_items(
            conn, contract_id=before["id"], requested=changes["line_items"]
        )

    after = await resolve_scoped(conn, "contracts", public_id, company_id)
    await audit.record(
        conn,
        action="contract.updated",
        resource_type="contract",
        resource_id=before["id"],
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        old_values={
            **{k: _jsonable(before.get(k)) for k in updates},
            "contract_value": metadata.get("contract_value"),
        },
        new_values={
            **{k: _jsonable(after.get(k)) for k in updates},
            "contract_value": new_metadata.get("contract_value"),
        },
        reason=changes.get("reason"),
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_contract(conn, company_id=company_id, public_id=public_id)


async def _clear_contract_roles(
    conn: AsyncConnection, *, contract_id: Any, project_id: Any
) -> None:
    """Detach contract roles, refusing while work already references them."""
    rows = (
        (
            await conn.execute(
                text(
                    """
                    SELECT cr.id, pr.public_id AS project_role_id
                      FROM public.contract_roles cr
                      JOIN public.project_roles pr ON pr.id = cr.project_role_id
                     WHERE cr.contract_id = :cid
                    """
                ),
                {"cid": contract_id},
            )
        )
        .mappings()
        .all()
    )
    if not rows:
        return

    blocked = await conn.execute(
        text(
            """
            SELECT cr.project_role_id::text AS prid, count(t.id) AS sheets
              FROM public.contract_roles cr
              JOIN public.timesheets t ON t.contract_role_id = cr.id
             WHERE cr.contract_id = :cid
               AND t.status NOT IN ('REJECTED','DRAFT')
             GROUP BY cr.project_role_id
            """
        ),
        {"cid": contract_id},
    )
    counts = blocked.mappings().all()
    if counts:
        raise BusinessRuleViolationError(
            "A role on this contract already has submitted timesheets and cannot be removed.",
            details={
                "reason": "CONTRACT_ROLE_IN_USE",
                "roles": [
                    {"project_role_id": r["prid"], "timesheets": int(as_decimal(r["sheets"]))}
                    for r in counts
                ],
            },
        )

    await conn.execute(
        text(
            """
            UPDATE public.sow_roles
               SET rate = NULL, notes = COALESCE(notes,'') || ' (role detached from contract)'
             WHERE id IN (
                   SELECT x.sow_role_id FROM public.contract_roles x
                    WHERE x.contract_id = :cid AND x.sow_role_id IS NOT NULL
             )
            """
        ),
        {"cid": contract_id},
    )
    await conn.execute(
        text(
            """
            UPDATE public.assignments SET status = 'COMPLETED'
             WHERE contract_role_id IN (
                   SELECT id FROM public.contract_roles WHERE contract_id = :cid)
               AND status IN ('PENDING','ACTIVE','ON_LEAVE')
            """
        ),
        {"cid": contract_id},
    )
    await conn.execute(
        text("DELETE FROM public.contract_roles WHERE contract_id = :cid"), {"cid": contract_id}
    )


# =============================================================================
# workflow
# =============================================================================
async def submit_for_review(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    notes: str | None,
) -> dict[str, Any]:
    """DRAFT -> internal review: build the approval chain and freeze the terms."""
    before = await resolve_scoped(conn, "contracts", public_id, company_id, lock=True)
    if str(before["status"]) != "DRAFT":
        raise InvalidStateTransitionError(
            f"A contract in status {before['status']} is not awaiting review.",
            details={"status": before["status"]},
        )

    roles = await conn.execute(
        text("SELECT count(*) FROM public.contract_roles WHERE contract_id = :cid"),
        {"cid": before["id"]},
    )
    if as_decimal(roles.scalar()) == 0:
        raise BusinessRuleViolationError(
            "A contract must cover at least one project role before it goes to review.",
            details={"reason": "CONTRACT_HAS_NO_ROLES"},
        )

    await _build_contract_approval_chain(conn, contract_id=before["id"], company_id=company_id)
    # Terms are frozen once review starts; unlocking later requires contracts.update.
    await conn.execute(
        text("UPDATE public.contracts SET locked = true WHERE id = :rid"), {"rid": before["id"]}
    )

    await audit.record(
        conn,
        action="contract.submitted_for_review",
        resource_type="contract",
        resource_id=before["id"],
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        new_values={"locked": True, "review": "OPEN"},
        reason=notes,
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_contract(conn, company_id=company_id, public_id=public_id)


async def approve_step(
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
    before = await resolve_scoped(conn, "contracts", public_id, company_id)
    row = (
        (
            await conn.execute(
                text(
                    """
                    UPDATE public.contract_approval_steps
                       SET status = :decision, decided_at = now(), notes = :notes
                     WHERE contract_id = :cid AND step_no = :step
                    RETURNING id::text
                    """
                ),
                {
                    "decision": decision,
                    "notes": notes,
                    "cid": before["id"],
                    "step": step_no,
                },
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ResourceNotFoundError("Approval step not found on this contract.")

    remaining = await conn.execute(
        text(
            """
            SELECT count(*) FROM public.contract_approval_steps
             WHERE contract_id = :cid AND status = 'PENDING'
            """
        ),
        {"cid": before["id"]},
    )
    approved_all = as_decimal(remaining.scalar()) == 0

    await audit.record(
        conn,
        action=f"contract.approval_{decision.lower()}",
        resource_type="contract",
        resource_id=before["id"],
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        new_values={"step_no": step_no, "status": decision, "fully_approved": approved_all},
        reason=notes,
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_contract(conn, company_id=company_id, public_id=public_id)


async def send_contract(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    notes: str | None,
) -> dict[str, Any]:
    before = await resolve_scoped(conn, "contracts", public_id, company_id, lock=True)
    current = str(before["status"])
    if current != "DRAFT":
        raise InvalidStateTransitionError(
            f"A contract in status {current} cannot be sent.",
            details={"status": current, "allowed": list(CONTRACT_TRANSITIONS.get(current, ()))},
        )

    pending = await conn.execute(
        text(
            """
            SELECT count(*) FROM public.contract_approval_steps
             WHERE contract_id = :cid AND status = 'PENDING'
            """
        ),
        {"cid": before["id"]},
    )
    if as_decimal(pending.scalar()) > 0:
        raise BusinessRuleViolationError(
            "This contract still has an open approval step.",
            details={"reason": "APPROVAL_CHAIN_INCOMPLETE"},
        )

    await conn.execute(
        text("UPDATE public.contracts SET status = 'SENT', sent_at = now() WHERE id = :rid"),
        {"rid": before["id"]},
    )
    await audit.record(
        conn,
        action="contract.sent",
        resource_type="contract",
        resource_id=before["id"],
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        old_values={"status": current},
        new_values={"status": "SENT"},
        reason=notes,
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_contract(conn, company_id=company_id, public_id=public_id)


async def _resolve_contract_for_decision(
    conn: AsyncConnection,
    *,
    public_id: str,
    user_id: uuid.UUID,
    company_id: uuid.UUID | None,
    lock: bool = False,
) -> dict[str, Any]:
    """Fetch a contract the caller may decide on: owning company, counterparty
    member company, counterparty user, or personal owner. Anything else is a
    404 — a foreign contract id must never confirm its own existence."""
    sql = (
        "SELECT c.id, c.public_id, c.status, c.company_id,"
        " c.counterparty_company_id, c.counterparty_user_id, c.created_by"
        " FROM public.contracts c WHERE c.public_id = :pid AND c.deleted_at IS NULL"
    )
    if lock:
        sql += " FOR UPDATE"
    row = (await conn.execute(text(sql), {"pid": public_id})).mappings().first()
    if row is None:
        raise ResourceNotFoundError("Contract not found.")
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
        raise ResourceNotFoundError("Contract not found.")
    return data


async def respond_to_contract(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID | None,
    public_id: str,
    accept: bool,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    notes: str | None,
) -> dict[str, Any]:
    """Counterparty acceptance (or rejection) of a sent contract."""
    before = await _resolve_contract_for_decision(
        conn, public_id=public_id, user_id=actor_user_id, company_id=company_id, lock=True
    )
    current = str(before["status"])
    target = "ACCEPTED" if accept else "DECLINED"
    if target not in CONTRACT_TRANSITIONS.get(current, ()):
        raise InvalidStateTransitionError(
            f"A contract in status {current} cannot be {target.lower()}.",
            details={"status": current, "allowed": list(CONTRACT_TRANSITIONS.get(current, ()))},
        )

    if accept and as_decimal(before.get("contract_value") or 0) == 0:
        roles = await conn.execute(
            text(
                """
                SELECT count(*) FROM public.contract_roles
                 WHERE contract_id = :cid AND rate IS NOT NULL
                """
            ),
            {"cid": before["id"]},
        )
        if as_decimal(roles.scalar()) == 0:
            raise BusinessRuleViolationError(
                "A contract cannot be accepted while none of its roles carry a rate.",
                details={"reason": "NO_COMMERCIAL_ROLES"},
            )

    await conn.execute(
        text(
            "UPDATE public.contracts SET status = :target, response_notes = :notes,"
            " responded_by = :actor, responded_at = now() WHERE id = :rid"
        ),
        {"target": target, "notes": notes, "actor": actor_user_id, "rid": before["id"]},
    )
    await conn.execute(
        text(
            """
            UPDATE public.contract_parties SET signed_at = now()
             WHERE contract_id = :cid
               AND (party_company_id IS NOT NULL OR party_user_id IS NOT NULL)
            """
        ),
        {"cid": before["id"]},
    )

    await audit.record(
        conn,
        action="contract.accepted" if accept else "contract.declined",
        resource_type="contract",
        resource_id=before["id"],
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        old_values={"status": current},
        new_values={"status": target},
        reason=notes,
        request_id=request_id,
        ip_address=ip_address,
    )
    return await _get_contract_after_decision(conn, public_id=public_id)


async def _get_contract_after_decision(conn: AsyncConnection, *, public_id: str) -> dict[str, Any]:
    """Read-back after accept/decline for any deciding party.

    The decision right was already verified; row visibility additionally passes
    through RLS (`can_view_contract` covers owner, counterparty member and
    counterparty user), so no party reads what it may not see.
    """
    row = (
        (
            await conn.execute(
                text(f"{_CONTRACT_SELECT} WHERE c.public_id = :pid"),
                {"pid": public_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ResourceNotFoundError("Contract not found.")
    roles, parties, line_items, steps = await _contract_children(conn, row["id"])
    data = _contract_from_row(row)
    data["roles"] = roles
    data["parties"] = parties
    data["line_items"] = line_items
    data["approval_steps"] = steps
    data["allowed_transitions"] = list(CONTRACT_TRANSITIONS.get(str(row["status"]), ()))
    return data


async def activate_contract(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    notes: str | None,
) -> dict[str, Any]:
    """ACCEPTED -> ACTIVE, which also switches on any pending assignments."""
    before = await resolve_scoped(conn, "contracts", public_id, company_id, lock=True)
    if str(before["status"]) != "ACCEPTED":
        raise InvalidStateTransitionError(
            f"A contract in status {before['status']} cannot be activated.",
            details={"status": before["status"], "allowed": ["ACTIVE"]},
        )

    await conn.execute(
        text("UPDATE public.contracts SET status = 'ACTIVE' WHERE id = :rid"), {"rid": before["id"]}
    )
    # `app.activate_assignments_on_contract` fires on the status change above and
    # switches PENDING assignments to ACTIVE, so no explicit call is made here.

    await audit.record(
        conn,
        action="contract.activated",
        resource_type="contract",
        resource_id=before["id"],
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        old_values={"status": "ACCEPTED"},
        new_values={"status": "ACTIVE"},
        reason=notes,
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_contract(conn, company_id=company_id, public_id=public_id)


async def terminate_contract(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    reason: str,
) -> dict[str, Any]:
    before = await resolve_scoped(conn, "contracts", public_id, company_id, lock=True)
    current = str(before["status"])
    if "TERMINATED" not in CONTRACT_TRANSITIONS.get(current, ()):
        raise InvalidStateTransitionError(
            f"A contract in status {current} cannot be terminated.",
            details={"status": current, "allowed": list(CONTRACT_TRANSITIONS.get(current, ()))},
        )

    open_invoices = await conn.execute(
        text(
            """
            SELECT count(*) FROM public.invoices
             WHERE contract_id = :cid AND direction = 'RECEIVABLE'
               AND status NOT IN ('PAID','CANCELLED','REFUNDED','REJECTED')
               AND deleted_at IS NULL
            """
        ),
        {"cid": before["id"]},
    )
    # A Result is single-use: read the scalar once.
    open_count = int(as_decimal(open_invoices.scalar()))
    if open_count > 0:
        raise BusinessRuleViolationError(
            "Settle or cancel the open invoices before terminating this contract.",
            details={"reason": "OPEN_INVOICES", "open_invoices": open_count},
        )

    notice = before.get("termination_notice_days")
    effective = utc_today()
    if notice:
        effective = utc_today() + timedelta(days=int(notice))

    await conn.execute(
        text(
            "UPDATE public.contracts SET status = 'TERMINATED', notice_period_end = :eff"
            " WHERE id = :rid"
        ),
        {"eff": effective, "rid": before["id"]},
    )
    await conn.execute(
        text(
            """
            UPDATE public.assignments SET status = 'TERMINATED', end_date = COALESCE(end_date, :eff)
             WHERE contract_id = :cid AND status IN ('PENDING','ACTIVE','ON_LEAVE')
            """
        ),
        {"cid": before["id"], "eff": effective},
    )

    await audit.record(
        conn,
        action="contract.terminated",
        resource_type="contract",
        resource_id=before["id"],
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        old_values={"status": current},
        new_values={"status": "TERMINATED", "notice_period_end": str(effective)},
        reason=reason,
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_contract(conn, company_id=company_id, public_id=public_id)


async def close_contract(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    reason: str,
) -> dict[str, Any]:
    before = await resolve_scoped(conn, "contracts", public_id, company_id, lock=True)
    if "CLOSED" not in CONTRACT_TRANSITIONS.get(str(before["status"]), ()):
        raise InvalidStateTransitionError(
            f"A contract in status {before['status']} cannot be closed.",
            details={"status": before["status"]},
        )
    await conn.execute(
        text("UPDATE public.contracts SET status = 'CLOSED' WHERE id = :rid"), {"rid": before["id"]}
    )
    await audit.record(
        conn,
        action="contract.closed",
        resource_type="contract",
        resource_id=before["id"],
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        old_values={"status": before["status"]},
        new_values={"status": "CLOSED"},
        reason=reason,
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_contract(conn, company_id=company_id, public_id=public_id)


async def renew_contract(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    new_start_date: date,
    new_end_date: date,
    contract_value: Any,
) -> dict[str, Any]:
    """Renew in place: roll the dates forward and record the new value.

    A renewal does not fork a new contract: history, timesheets and invoices stay
    attached, and the version counter increments so the change is traceable.
    """
    before = await resolve_scoped(conn, "contracts", public_id, company_id, lock=True)
    current = str(before["status"])
    if current not in {"ACTIVE", "EXPIRED", "ACCEPTED"}:
        raise InvalidStateTransitionError(
            f"A contract in status {current} cannot be renewed.",
            details={"status": current, "renewable_in": ["ACCEPTED", "ACTIVE", "EXPIRED"]},
        )
    if new_end_date < new_start_date:
        raise ValidationError("The renewal end date must not precede the start date.")

    metadata = json_or_empty(before.get("metadata"))
    history = list(metadata.get("renewals") or [])
    history.append(
        {
            "from": str(before["start_date"]),
            "to": str(before["end_date"]),
            "renewed_from": str(new_start_date),
            "renewed_to": str(new_end_date),
            "at": str(utc_today()),
        }
    )
    metadata["renewals"] = history
    if contract_value is not None:
        from app.services.billing import money

        metadata["contract_value"] = str(money(contract_value))

    await conn.execute(
        text(
            """
            UPDATE public.contracts
               SET start_date = :start_date, end_date = :end_date,
                   status = 'ACTIVE', version = version + 1,
                   metadata = CAST(:meta AS jsonb)
             WHERE id = :rid
            """
        ),
        {
            "start_date": new_start_date,
            "end_date": new_end_date,
            "meta": _json(metadata),
            "rid": before["id"],
        },
    )
    await audit.record(
        conn,
        action="contract.renewed",
        resource_type="contract",
        resource_id=before["id"],
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        old_values={"start_date": before["start_date"], "end_date": before["end_date"]},
        new_values={
            "start_date": new_start_date,
            "end_date": new_end_date,
            "contract_value": metadata.get("contract_value"),
        },
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_contract(conn, company_id=company_id, public_id=public_id)


async def contract_versions(
    conn: AsyncConnection, *, company_id: uuid.UUID, public_id: str
) -> list[dict[str, Any]]:
    """Version history reconstructed from the audit trail.

    Contract terms are frozen once review starts, so the audit log *is* the
    version record; no second copy of commercial terms is stored.
    """
    await resolve_scoped(conn, "contracts", public_id, company_id, columns="id")
    rows = (
        (
            await conn.execute(
                text(
                    """
                    SELECT a.action, a.old_values, a.new_values, a.reason,
                           a.occurred_at AS created_at,
                           u.public_id AS actor_public_id,
                           NULLIF(TRIM(u.first_name || ' ' || u.last_name), '') AS actor_name
                      FROM platform.audit_logs a
                      LEFT JOIN public.users u ON u.id = a.actor_user_id
                     WHERE a.resource_type = 'contract'
                       AND a.resource_public_id = :pid
                       AND a.company_id = :cid
                       AND a.action IN
                           ('contract.created','contract.updated','contract.submitted_for_review',
                            'contract.sent','contract.accepted','contract.declined',
                            'contract.activated','contract.renewed','contract.terminated',
                            'contract.closed')
                     ORDER BY a.occurred_at DESC
                     LIMIT 200
                    """
                ),
                {"pid": public_id, "cid": company_id},
            )
        )
        .mappings()
        .all()
    )
    history: list[dict[str, Any]] = [
        {
            "action": r["action"],
            "version": idx + 1,
            "changed_at": r["created_at"],
            "changed_by": r["actor_public_id"],
            "changed_by_name": r["actor_name"],
            "reason": r["reason"],
            "snapshot": {
                "before": json_or_empty(r["old_values"]),
                "after": json_or_empty(r["new_values"]),
            },
        }
        for idx, r in enumerate(rows)
    ]
    return history


async def contract_roles(
    conn: AsyncConnection, *, company_id: uuid.UUID, public_id: str
) -> list[dict[str, Any]]:
    contract = await get_contract(conn, company_id=company_id, public_id=public_id)
    roles: list[dict[str, Any]] = contract["roles"]
    return roles


# =============================================================================
# personal (INDIVIDUAL) contracts — no company, owned by the creator
# =============================================================================
async def _personal_contract_read(
    conn: AsyncConnection, *, user_id: uuid.UUID, public_id: str
) -> dict[str, Any]:
    """Read a personal contract for its owner or its counterparty user."""
    row = (
        (
            await conn.execute(
                text(
                    f"""{_CONTRACT_SELECT}
                     WHERE c.public_id = :pid AND c.company_id IS NULL
                       AND c.deleted_at IS NULL
                       AND (c.created_by = :uid OR c.counterparty_user_id = :uid)"""
                ),
                {"pid": public_id, "uid": user_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ResourceNotFoundError("Contract not found.")
    roles, parties, line_items, steps = await _contract_children(conn, row["id"])
    data = _contract_from_row(row)
    data["roles"] = roles
    data["parties"] = parties
    data["line_items"] = line_items
    data["approval_steps"] = steps
    data["allowed_transitions"] = list(CONTRACT_TRANSITIONS.get(str(row["status"]), ()))
    return data


async def _resolve_personal_contract_roles(
    conn: AsyncConnection,
    *,
    user_id: uuid.UUID,
    project_id: uuid.UUID,
    requested: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in requested:
        role_public_id = item["project_role_id"]
        if role_public_id in seen:
            raise ValidationError(
                "The same project role appears twice on this contract.",
                details={"project_role_id": role_public_id},
            )
        seen.add(role_public_id)

        role = await resolve_personal(conn, "project_roles", role_public_id, user_id)
        if str(role["project_id"]) != str(project_id):
            raise BusinessRuleViolationError(
                "That role does not belong to this contract's project.",
                details={
                    "project_role_id": role_public_id,
                    "reason": "PROJECT_ROLE_NOT_ON_PROJECT",
                },
            )
        out.append({**item, "project_role_id": role["id"]})
    return out


async def create_personal_contract(
    conn: AsyncConnection,
    *,
    user_id: uuid.UUID,
    sow_public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Create one engagement contract under the caller's personal SOW (§19).

    Each actual assignment gets its own contract; the SOW stays the commercial
    allocation layer and the contract the binding engagement.
    """
    from app.services import code as code_service

    sow = await code_service.get_personal_sow(conn, user_id=user_id, public_id=sow_public_id)
    if payload.get("project_id") and str(payload["project_id"]) != str(sow["project_id"]):
        raise BusinessRuleViolationError(
            "The selected SOW belongs to a different project.",
            details={"reason": "SOW_PROJECT_MISMATCH"},
        )
    project_id = await _personal_project_uuid(conn, user_id, sow["project_id"])

    counterparty_user = None
    if payload.get("counterparty_user_id"):
        counterparty_user = await resolve_user_public_id(conn, payload["counterparty_user_id"])
    elif sow.get("counterparty_user_id"):
        # _sow_from_row exposes the counterparty as a public U... id.
        counterparty_user = await resolve_user_public_id(conn, sow["counterparty_user_id"])

    roles = await _resolve_personal_contract_roles(
        conn,
        user_id=user_id,
        project_id=project_id,
        requested=payload.get("roles") or [],
    )
    if not roles:
        raise ValidationError(
            "A contract needs at least one role from its SOW.",
            details={"reason": "CONTRACT_HAS_NO_ROLES"},
        )

    from app.services.billing import money

    contract_value = payload.get("contract_value")
    metadata = {
        "contract_value": str(money(contract_value)) if contract_value is not None else None,
        "terms_snapshot": payload.get("terms_snapshot") or {},
    }

    row = (
        (
            await conn.execute(
                text(
                    """
                    INSERT INTO public.contracts
                      (sow_id, project_id, company_id, contract_type, title, status,
                       currency, billing_basis, billing_frequency, payment_terms_days,
                       start_date, end_date, counterparty_user_id,
                       requires_timesheets, metadata, created_by)
                    VALUES
                      (:sow, :pid, NULL, 'INDIVIDUAL', :title, 'DRAFT',
                       :currency, :basis, :frequency, :terms,
                       :start_date, :end_date, CAST(:counterparty AS uuid),
                       :requires_timesheets, CAST(:metadata AS jsonb), :actor)
                    RETURNING public_id
                    """
                ),
                {
                    "sow": sow["id"],
                    "pid": project_id,
                    "title": payload["title"],
                    "currency": payload.get("currency", "USD"),
                    "basis": payload.get("billing_basis", "TIMESHEET"),
                    "frequency": payload.get("billing_frequency", "MONTHLY"),
                    "terms": payload.get("payment_terms_days", 30),
                    "start_date": payload.get("start_date") or sow.get("start_date"),
                    "end_date": payload.get("end_date") or sow.get("end_date"),
                    "counterparty": counterparty_user,
                    "requires_timesheets": payload.get("requires_timesheets", True),
                    "metadata": _json(metadata),
                    "actor": actor_user_id,
                },
            )
        )
        .mappings()
        .first()
    )
    contract_id = uuid.UUID(
        str(
            (
                await conn.execute(
                    text("SELECT id FROM public.contracts WHERE public_id = :pid"),
                    {"pid": str(row["public_id"])},
                )
            ).scalar_one()
        )
    )

    await _insert_contract_roles(
        conn,
        contract_id=contract_id,
        company_id=user_id,  # unused by the insert; kept for signature parity
        requested=roles,
        default_currency=payload.get("currency", "USD"),
        default_basis=payload.get("billing_basis", "TIMESHEET"),
        default_frequency=payload.get("billing_frequency", "MONTHLY"),
        default_terms=payload.get("payment_terms_days", 30),
        start_date=payload.get("start_date"),
        end_date=payload.get("end_date"),
    )
    await conn.execute(
        text(
            """
            INSERT INTO public.contract_parties (contract_id, party_user_id, party_role)
            VALUES (:cid, :owner, 'PRIMARY'), (:cid, CAST(:counterparty AS uuid), 'COUNTERPARTY')
            """
        ),
        {"cid": contract_id, "owner": actor_user_id, "counterparty": counterparty_user},
    )
    await _insert_contract_line_items(
        conn, contract_id=contract_id, requested=payload.get("line_items") or []
    )

    await audit.record(
        conn,
        action="contract.created",
        resource_type="contract",
        resource_id=contract_id,
        resource_public_id=str(row["public_id"]),
        actor_user_id=actor_user_id,
        new_values={
            "sow_id": sow_public_id,
            "title": payload["title"],
            "role_count": len(roles),
        },
        request_id=request_id,
        ip_address=ip_address,
    )
    return await _personal_contract_read(conn, user_id=user_id, public_id=str(row["public_id"]))


async def get_personal_contract(
    conn: AsyncConnection, *, user_id: uuid.UUID, public_id: str
) -> dict[str, Any]:
    """Owner-or-counterparty read of one personal contract."""
    return await _personal_contract_read(conn, user_id=user_id, public_id=public_id)


async def _personal_project_uuid(
    conn: AsyncConnection, user_id: uuid.UUID, project_public_id: str
) -> uuid.UUID:
    from app.services import code as code_service

    project = await code_service.get_personal_project(
        conn, user_id=user_id, public_id=project_public_id
    )
    row = (
        await conn.execute(
            text("SELECT id FROM public.projects WHERE public_id = :pid"),
            {"pid": project["public_id"]},
        )
    ).scalar_one()
    return uuid.UUID(str(row))


async def create_personal_contract_from_sow(
    conn: AsyncConnection,
    *,
    sow_public_id: str,
    owner_user_id: uuid.UUID,
    request_id: str,
) -> dict[str, Any]:
    """§17: an accepted individual SOW automatically gains its engagement
    contract, so no SOW is ever an orphan that cannot become executable."""
    from app.services import code as code_service

    sow = await code_service.get_personal_sow(conn, user_id=owner_user_id, public_id=sow_public_id)
    roles = [
        {
            "project_role_id": r["project_role_id"],
            "quantity": r.get("quantity", 1),
            "rate": r.get("rate"),
            "rate_type": r.get("rate_type", "HOURLY"),
            "currency": r.get("currency", sow.get("currency", "USD")),
        }
        for r in sow.get("roles") or []
    ]
    contract = await create_personal_contract(
        conn,
        user_id=owner_user_id,
        sow_public_id=sow_public_id,
        actor_user_id=owner_user_id,
        request_id=request_id,
        ip_address=None,
        payload={
            "title": f"{sow['title']} — engagement",
            "counterparty_user_id": sow.get("counterparty_user_id"),
            "currency": sow.get("currency", "USD"),
            "billing_basis": sow.get("billing_basis", "TIMESHEET"),
            "billing_frequency": sow.get("billing_frequency", "MONTHLY"),
            "payment_terms_days": sow.get("payment_terms_days", 30),
            "start_date": sow.get("start_date"),
            "end_date": sow.get("end_date"),
            "roles": roles,
        },
    )
    # Mutual SOW acceptance is mutual contract agreement.
    await conn.execute(
        text(
            "UPDATE public.contracts SET status = 'ACTIVE', activated_at = now()"
            " WHERE public_id = :pid"
        ),
        {"pid": contract["public_id"]},
    )
    internal_id = (
        await conn.execute(
            text("SELECT id FROM public.contracts WHERE public_id = :pid"),
            {"pid": contract["public_id"]},
        )
    ).scalar_one()
    await audit.record(
        conn,
        action="contract.activated",
        resource_type="contract",
        resource_id=internal_id,
        resource_public_id=contract["public_id"],
        actor_user_id=owner_user_id,
        old_values={"status": "DRAFT"},
        new_values={"status": "ACTIVE", "via": "sow_acceptance"},
        request_id=request_id,
    )
    return await _personal_contract_read(
        conn, user_id=owner_user_id, public_id=contract["public_id"]
    )


async def list_personal_contracts(
    conn: AsyncConnection, *, user_id: uuid.UUID, limit: int
) -> list[dict[str, Any]]:
    rows = (
        (
            await conn.execute(
                text(
                    f"""{_CONTRACT_SELECT}
                     WHERE c.company_id IS NULL AND c.deleted_at IS NULL
                       AND (c.created_by = :uid OR c.counterparty_user_id = :uid)
                     ORDER BY c.created_at DESC
                     LIMIT :limit"""
                ),
                {"uid": user_id, "limit": limit},
            )
        )
        .mappings()
        .all()
    )
    out = []
    for r in rows:
        data = _contract_from_row(r)
        data["roles"] = []  # list view stays light; detail carries children
        out.append(data)
    return out


async def update_personal_contract(
    conn: AsyncConnection,
    *,
    user_id: uuid.UUID,
    public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    changes: dict[str, Any],
) -> dict[str, Any]:
    before = await resolve_personal_contract(conn, user_id, public_id, lock=True)
    if str(before["status"]) != "DRAFT":
        raise InvalidStateTransitionError(
            "Commercial terms can only be edited while the contract is a draft.",
            details={"status": before["status"]},
        )
    updates = {k: v for k, v in changes.items() if k in _CONTRACT_UPDATABLE and v is not None}
    if updates:
        assignments = ", ".join(f"{col} = :{col}" for col in updates)
        await conn.execute(
            text(f"UPDATE public.contracts SET {assignments} WHERE id = :rid"),  # noqa: S608
            {**updates, "rid": before["id"]},
        )
    after = await _personal_contract_read(conn, user_id=user_id, public_id=public_id)
    await audit.record(
        conn,
        action="contract.updated",
        resource_type="contract",
        resource_id=before["id"],
        resource_public_id=public_id,
        actor_user_id=actor_user_id,
        old_values={k: _jsonable(before.get(k)) for k in updates},
        new_values={k: _jsonable(after.get(k)) for k in updates},
        request_id=request_id,
        ip_address=ip_address,
    )
    return after


async def resolve_personal_contract(
    conn: AsyncConnection, user_id: uuid.UUID, public_id: str, *, lock: bool = False
) -> dict[str, Any]:
    """Owner-scoped personal contract lookup for mutations."""
    sql = (
        "SELECT c.id, c.public_id, c.project_id, c.sow_id, c.status, c.title,"
        " c.currency, c.billing_basis, c.billing_frequency, c.payment_terms_days,"
        " c.start_date, c.end_date, c.counterparty_user_id, c.created_by"
        " FROM public.contracts c WHERE c.public_id = :pid"
        " AND c.company_id IS NULL AND c.created_by = :uid"
    )
    if lock:
        sql += " FOR UPDATE"
    row = (await conn.execute(text(sql), {"pid": public_id, "uid": user_id})).mappings().first()
    if row is None:
        raise ResourceNotFoundError("Contract not found.")
    return dict(row)


async def transition_personal_contract(
    conn: AsyncConnection,
    *,
    user_id: uuid.UUID,
    public_id: str,
    target: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    reason: str | None = None,
) -> dict[str, Any]:
    """DRAFT→SENT→ACCEPTED→ACTIVE plus TERMINATED/CLOSED, same map as company."""
    before = await _resolve_contract_for_decision(
        conn, public_id=public_id, user_id=actor_user_id, company_id=None, lock=True
    )
    if before["company_id"] is not None:
        raise ResourceNotFoundError("Contract not found.")
    current = str(before["status"])
    if target == "SENT" and current != "DRAFT":
        raise InvalidStateTransitionError(
            f"A personal contract in status {current} cannot be sent.",
            details={"status": current},
        )
    elif target != "SENT" and target not in CONTRACT_TRANSITIONS.get(current, ()):
        raise InvalidStateTransitionError(
            f"A contract in status {current} cannot move to {target}.",
            details={"status": current, "allowed": list(CONTRACT_TRANSITIONS.get(current, ()))},
        )
    if target == "TERMINATED" and not (reason or "").strip():
        raise ValidationError(
            "Terminating a contract requires a reason.",
            details={"reason": "REASON_REQUIRED"},
        )
    await conn.execute(
        text(
            "UPDATE public.contracts SET status = CAST(:target AS contract_status),"
            " sent_at = CASE WHEN :target = 'SENT' THEN now() ELSE sent_at END,"
            " activated_at = CASE WHEN :target = 'ACTIVE' THEN now() ELSE activated_at END,"
            " terminated_at = CASE WHEN :target = 'TERMINATED' THEN now() ELSE terminated_at END"
            " WHERE id = :rid"
        ),
        {"target": target, "rid": before["id"]},
    )
    await audit.record(
        conn,
        action=f"contract.{target.lower()}",
        resource_type="contract",
        resource_id=before["id"],
        resource_public_id=public_id,
        actor_user_id=actor_user_id,
        old_values={"status": current},
        new_values={"status": target},
        reason=reason,
        request_id=request_id,
        ip_address=ip_address,
    )
    return await _personal_contract_read(conn, user_id=user_id, public_id=public_id)
