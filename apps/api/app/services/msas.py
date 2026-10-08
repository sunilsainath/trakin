"""Master Service Agreements.

An MSA is a bilateral agreement between two companies. It gates invoice
submission (`app.has_active_msa` and `ck_invoice_msa_gate`), so the workflow is:

    NO_MSA -> REQUESTED -> UNDER_REVIEW -> ACTIVE
                                 -> REJECTED / WITHDRAWN
    ACTIVE -> EXPIRED / TERMINATED, and ACTIVE -> ACTIVE on renewal

Versions are immutable; each is submitted and reviewed on its own, and
`sync_msa_current_version` promotes the latest accepted version.
"""

from __future__ import annotations

import uuid
from datetime import date
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
from app.services.lookup import json_or_empty, resolve_company_public_id

logger = get_logger(__name__)

MSA_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "NO_MSA": ("REQUESTED",),
    "REQUESTED": ("UNDER_REVIEW", "ACTIVE", "REJECTED", "WITHDRAWN"),
    "UNDER_REVIEW": ("ACTIVE", "REJECTED", "WITHDRAWN"),
    "ACTIVE": ("EXPIRED", "TERMINATED"),
    "REJECTED": ("REQUESTED",),
    "WITHDRAWN": ("REQUESTED",),
    "EXPIRED": ("REQUESTED",),
    "TERMINATED": ("REQUESTED",),
}

MSA_VERSION_STATUSES = ("DRAFT", "SUBMITTED", "UNDER_REVIEW", "ACCEPTED", "REJECTED", "SUPERSEDED")


def _assert_transition(current: str, target: str) -> None:
    if target not in MSA_TRANSITIONS.get(current, ()):
        raise InvalidStateTransitionError(
            f"An MSA in status {current} cannot move to {target}.",
            details={"status": current, "allowed": list(MSA_TRANSITIONS.get(current, ()))},
        )


_MSA_SELECT = """
    SELECT m.id, m.public_id, m.company_a_id, m.company_b_id, m.status, m.effective_date,
           m.expiration_date, m.auto_renew, m.renewal_notice_days, m.governing_law,
           m.payment_terms_days, m.current_version_id, m.activated_at, m.terminated_at,
           m.notes, m.metadata, m.created_at, m.updated_at,
           ca.public_id AS company_a_public_id,
           COALESCE(ca.display_name, ca.legal_name) AS company_a_name,
           cb.public_id AS company_b_public_id,
           COALESCE(cb.display_name, cb.legal_name) AS company_b_name,
           ru.public_id AS requested_by,
           COALESCE(v.version_no, 0) AS current_version_no,
           v.status AS current_version_status,
           v.effective_date AS version_effective_date,
           v.expiration_date AS version_expiration_date
      FROM public.msas m
      JOIN public.companies ca ON ca.id = m.company_a_id
      JOIN public.companies cb ON cb.id = m.company_b_id
      LEFT JOIN public.users ru ON ru.id = m.requested_by
      LEFT JOIN public.msa_versions v ON v.id = m.current_version_id
"""


def _msa_from_row(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["company_a_id"] = data.pop("company_a_public_id")
    data["company_b_id"] = data.pop("company_b_public_id")
    data.pop("id", None)
    metadata = json_or_empty(data.pop("metadata", None))
    data["metadata"] = metadata
    return data


async def list_msas(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    status: str | None = None,
    counterparty_company_id: str | None = None,
    expiring_within_days: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """MSAs the caller's company is a party to.

    The predicate is party membership rather than `company_id = :cid`, because
    either party may own the record.
    """
    where = ["app.is_member(m.company_a_id) OR app.is_member(m.company_b_id)"]
    params: dict[str, Any] = {"limit": limit, "offset": offset}

    if status:
        where.append("m.status = :status")
        params["status"] = status
    if counterparty_company_id:
        other = await resolve_company_public_id(conn, counterparty_company_id)
        where.append("(m.company_a_id = :other OR m.company_b_id = :other)")
        params["other"] = other
    if expiring_within_days:
        where.append(
            "m.status = 'ACTIVE' AND m.expiration_date IS NOT NULL"
            " AND m.expiration_date <= current_date + :window"
        )
        params["window"] = expiring_within_days

    rows = (
        (
            await conn.execute(
                text(
                    f"""
                    {_MSA_SELECT}
                     WHERE {" AND ".join(where)}
                     ORDER BY m.updated_at DESC
                     LIMIT :limit OFFSET :offset
                    """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    out = []
    for row in rows:
        entry = _msa_from_row(row)
        entry["versions"] = await list_versions(conn, msa_id=row["id"])
        entry["requests"] = await list_requests(conn, msa_id=row["id"])
        entry["allowed_transitions"] = list(MSA_TRANSITIONS.get(str(row["status"]), ()))
        out.append(entry)
    return out


async def get_msa(
    conn: AsyncConnection, *, company_id: uuid.UUID, public_id: str
) -> dict[str, Any]:
    row = (
        (
            await conn.execute(
                text(f"{_MSA_SELECT} WHERE m.public_id = :pid"),
                {"pid": public_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ResourceNotFoundError("MSA not found.")
    allowed = await conn.execute(
        text("SELECT app.can_read_msa(CAST(:mid AS uuid))"), {"mid": row["id"]}
    )
    if not allowed.scalar():
        raise ResourceNotFoundError("MSA not found.")

    data = _msa_from_row(row)
    data["versions"] = await list_versions(conn, msa_id=row["id"])
    data["requests"] = await list_requests(conn, msa_id=row["id"])
    data["contracts"] = await _msa_contracts(conn, msa_id=row["id"])
    data["allowed_transitions"] = list(MSA_TRANSITIONS.get(str(row["status"]), ()))
    return data


async def _msa_contracts(conn: AsyncConnection, *, msa_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        (
            await conn.execute(
                text(
                    """
                    SELECT c.public_id, c.title, c.status,
                           i.public_id AS invoice_public_id
                      FROM public.contracts c
                      LEFT JOIN public.invoices i ON i.msa_id = c.id
                     WHERE c.deleted_at IS NULL
                       AND (c.company_id IN (SELECT company_a_id FROM public.msas WHERE id = :mid)
                            OR c.counterparty_company_id IN
                               (SELECT company_b_id FROM public.msas WHERE id = :mid))
                     ORDER BY c.created_at DESC LIMIT 50
                    """
                ),
                {"mid": msa_id},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def list_versions(conn: AsyncConnection, *, msa_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        (
            await conn.execute(
                text(
                    """
                    SELECT v.version_no, v.status, v.submitted_at, v.reviewed_at,
                           v.review_notes, v.effective_date, v.expiration_date,
                           v.document_version_id, v.created_at,
                           su.public_id AS submitted_by,
                           ru.public_id AS reviewed_by
                      FROM public.msa_versions v
                      LEFT JOIN public.users su ON su.id = v.submitted_by
                      LEFT JOIN public.users ru ON ru.id = v.reviewed_by
                     WHERE v.msa_id = :mid
                     ORDER BY v.version_no DESC
                    """
                ),
                {"mid": msa_id},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def list_requests(conn: AsyncConnection, *, msa_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        (
            await conn.execute(
                text(
                    """
                    SELECT r.status, r.message, r.template_key, r.expires_at,
                           r.response_notes, r.created_at,
                           rc.public_id AS requester_company_id,
                           COALESCE(rc.display_name, rc.legal_name) AS requester_company_name,
                           tc.public_id AS target_company_id,
                           COALESCE(tc.display_name, tc.legal_name) AS target_company_name
                      FROM public.msa_requests r
                      JOIN public.companies rc ON rc.id = r.requester_company_id
                      JOIN public.companies tc ON tc.id = r.target_company_id
                     WHERE r.msa_id = :mid
                     ORDER BY r.created_at DESC
                    """
                ),
                {"mid": msa_id},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def find_or_create_msa(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    counterparty_public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    governing_law: str | None = None,
    payment_terms_days: int = 30,
    auto_renew: bool = False,
    renewal_notice_days: int | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Get the MSA for this company pair, creating a `NO_MSA` placeholder if needed."""
    other = await resolve_company_public_id(conn, counterparty_public_id)
    if other == company_id:
        raise ValidationError(
            "An MSA needs two different companies.",
            details={"reason": "SAME_COMPANY"},
        )

    existing = (
        (
            await conn.execute(
                text(
                    """
                    SELECT public_id FROM public.msas
                     WHERE (company_a_id = :a AND company_b_id = :b)
                        OR (company_a_id = :b AND company_b_id = :a)
                    """
                ),
                {"a": company_id, "b": other},
            )
        )
        .mappings()
        .first()
    )
    if existing:
        return await get_msa(conn, company_id=company_id, public_id=str(existing["public_id"]))

    row = (
        (
            await conn.execute(
                text(
                    """
                    INSERT INTO public.msas
                      (pair_low, pair_high, company_a_id, company_b_id, status,
                       governing_law, payment_terms_days, auto_renew,
                       renewal_notice_days, notes, requested_by)
                    VALUES
                      (LEAST(:a, :b), GREATEST(:a, :b), :a, :b, 'NO_MSA',
                       :law, :terms, :auto_renew, :renewal_notice, :notes, :actor)
                    RETURNING public_id
                    """
                ),
                {
                    "a": company_id,
                    "b": other,
                    "law": governing_law,
                    "terms": payment_terms_days,
                    "auto_renew": auto_renew,
                    "renewal_notice": renewal_notice_days,
                    "notes": notes,
                    "actor": actor_user_id,
                },
            )
        )
        .mappings()
        .first()
    )
    await audit.record(
        conn,
        action="msa.created",
        resource_type="msa",
        resource_public_id=str(row["public_id"]),
        company_id=company_id,
        actor_user_id=actor_user_id,
        new_values={"counterparty_company_id": counterparty_public_id, "status": "NO_MSA"},
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_msa(conn, company_id=company_id, public_id=str(row["public_id"]))


async def _msa_row(conn: AsyncConnection, public_id: str) -> dict[str, Any]:
    row = (
        (
            await conn.execute(
                text(
                    "SELECT id, public_id, company_a_id, company_b_id, status, current_version_id"
                    " FROM public.msas WHERE public_id = :pid"
                ),
                {"pid": public_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ResourceNotFoundError("MSA not found.")
    allowed = await conn.execute(
        text("SELECT app.can_read_msa(CAST(:mid AS uuid))"), {"mid": row["id"]}
    )
    if not allowed.scalar():
        raise ResourceNotFoundError("MSA not found.")
    return dict(row)


async def create_version(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    version_no: int | None = None,
    effective_date: date | None = None,
    expiration_date: date | None = None,
    document_version_id: str | None = None,
) -> dict[str, Any]:
    """Add a new draft version. Previous versions are superseded, never edited."""
    msa = await _msa_row(conn, public_id)
    if str(msa["status"]) in {"ACTIVE"}:
        # A renewal is a new version; a live agreement is not rewritten.
        logger.info("msa_new_version_for_active", msa=public_id)

    next_no = version_no
    if next_no is None:
        row = (
            await conn.execute(
                text(
                    "SELECT COALESCE(max(version_no), 0) + 1 FROM public.msa_versions"
                    " WHERE msa_id = :mid"
                ),
                {"mid": msa["id"]},
            )
        ).scalar()
        next_no = int(row or 1)

    document_version_uuid = None
    if document_version_id:
        row = (
            (
                await conn.execute(
                    text(
                        "SELECT id::text FROM public.document_versions"
                        " WHERE id = CAST(:vid AS uuid)"
                    ),
                    {"vid": document_version_id},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ResourceNotFoundError("Document version not found.")
        document_version_uuid = row["id"]

    await conn.execute(
        text(
            """
            UPDATE public.msa_versions SET status = 'SUPERSEDED'
             WHERE msa_id = :mid AND status IN ('DRAFT','SUBMITTED','UNDER_REVIEW')
            """
        ),
        {"mid": msa["id"]},
    )
    await conn.execute(
        text(
            """
            INSERT INTO public.msa_versions
              (msa_id, version_no, status, effective_date, expiration_date,
               document_version_id)
            VALUES (:mid, :version, 'DRAFT', :effective, :expiration, CAST(:doc AS uuid))
            """
        ),
        {
            "mid": msa["id"],
            "version": next_no,
            "effective": effective_date,
            "expiration": expiration_date,
            "doc": document_version_uuid,
        },
    )

    await audit.record(
        conn,
        action="msa.version_created",
        resource_type="msa",
        resource_id=msa["id"],
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        new_values={
            "version_no": next_no,
            "effective_date": effective_date,
            "expiration_date": expiration_date,
        },
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_msa(conn, company_id=company_id, public_id=public_id)


async def transition(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    target: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    reason: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    msa = await _msa_row(conn, public_id)
    current = str(msa["status"])
    _assert_transition(current, target)

    if target == "ACTIVE":
        pending = await conn.execute(
            text(
                """
                SELECT count(*) FROM public.msa_versions
                 WHERE msa_id = :mid AND status IN ('DRAFT','SUBMITTED','UNDER_REVIEW')
                """
            ),
            {"mid": msa["id"]},
        )
        if int(pending.scalar() or 0) > 0:
            raise BusinessRuleViolationError(
                "Review the latest MSA version before activating.",
                details={
                    "reason": "VERSION_NOT_REVIEWED",
                    "pending_versions": int(pending.scalar() or 0),
                },
            )
        dates = await conn.execute(
            text(
                """
                SELECT effective_date, expiration_date FROM public.msa_versions
                 WHERE msa_id = :mid AND status = 'ACCEPTED'
                 ORDER BY version_no DESC LIMIT 1
                """
            ),
            {"mid": msa["id"]},
        )
        row = dates.mappings().first()
        if not row or not row["effective_date"] or not row["expiration_date"]:
            raise BusinessRuleViolationError(
                "An active MSA needs an effective date and an expiration date.",
                details={"reason": "MSA_DATES_MISSING"},
            )

    await conn.execute(
        text("UPDATE public.msas SET status = :target, reviewed_by = :actor WHERE id = :rid"),
        {"target": target, "actor": actor_user_id, "rid": msa["id"]},
    )

    await audit.record(
        conn,
        action=f"msa.{target.lower()}",
        resource_type="msa",
        resource_id=msa["id"],
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        old_values={"status": current},
        new_values={"status": target},
        reason=reason,
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_msa(conn, company_id=company_id, public_id=public_id)


async def review_version(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    version_no: int,
    decision: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    notes: str | None,
) -> dict[str, Any]:
    msa = await _msa_row(conn, public_id)
    updated = await conn.execute(
        text(
            """
            UPDATE public.msa_versions
               SET status = :decision, reviewed_by = :actor, reviewed_at = now(),
                   review_notes = :notes
             WHERE msa_id = :mid AND version_no = :version
               AND status IN ('DRAFT','SUBMITTED','UNDER_REVIEW')
            RETURNING version_no
            """
        ),
        {
            "decision": decision,
            "actor": actor_user_id,
            "notes": notes,
            "mid": msa["id"],
            "version": version_no,
        },
    )
    if updated.mappings().first() is None:
        raise ResourceNotFoundError("MSA version not found, or it has already been decided.")

    if decision == "ACCEPTED":
        await conn.execute(
            text(
                "UPDATE public.msa_versions SET status = 'SUPERSEDED'"
                " WHERE msa_id = :mid AND version_no < :version"
                "   AND status IN ('ACCEPTED','SUPERSEDED') AND version_no <> :version"
            ),
            {"mid": msa["id"], "version": version_no},
        )
        await conn.execute(
            text(
                """
                UPDATE public.msa_versions SET status = 'SUPERSEDED'
                 WHERE msa_id = :mid AND version_no < :version AND status = 'ACCEPTED'
                """
            ),
            {"mid": msa["id"], "version": version_no},
        )

    await audit.record(
        conn,
        action=f"msa.version_{decision.lower()}",
        resource_type="msa",
        resource_id=msa["id"],
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        new_values={"version_no": version_no, "decision": decision},
        reason=notes,
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_msa(conn, company_id=company_id, public_id=public_id)


async def create_request(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    message: str | None,
    template_key: str | None,
) -> dict[str, Any]:
    """Ask the counterparty to put an MSA in place."""
    msa = await _msa_row(conn, public_id)
    if str(msa["status"]) == "ACTIVE":
        raise BusinessRuleViolationError(
            "This pair already has an active MSA.",
            details={"reason": "MSA_ALREADY_ACTIVE"},
        )

    other = msa["company_b_id"] if msa["company_a_id"] == company_id else msa["company_a_id"]
    row = (
        (
            await conn.execute(
                text(
                    """
                    INSERT INTO public.msa_requests
                      (msa_id, requester_company_id, target_company_id, requested_by,
                       message, template_key)
                    VALUES (:mid, CAST(:requester AS uuid), CAST(:target AS uuid), :actor,
                            :message, :template)
                    RETURNING id::text
                    """
                ),
                {
                    "mid": msa["id"],
                    "requester": company_id,
                    "target": other,
                    "actor": actor_user_id,
                    "message": message,
                    "template": template_key,
                },
            )
        )
        .mappings()
        .first()
    )

    if str(msa["status"]) == "NO_MSA":
        await conn.execute(
            text("UPDATE public.msas SET status = 'MSA_REQUESTED' WHERE id = :rid"),
            {"rid": msa["id"]},
        )

    await audit.record(
        conn,
        action="msa.requested",
        resource_type="msa",
        resource_id=msa["id"],
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        new_values={"request_id": str(row["id"]), "message": message},
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_msa(conn, company_id=company_id, public_id=public_id)


async def renew(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    effective_date: date,
    expiration_date: date,
) -> dict[str, Any]:
    """Renew by adding a new version and re-activating."""
    if expiration_date < effective_date:
        raise ValidationError("The expiration date must not precede the effective date.")

    await create_version(
        conn,
        company_id=company_id,
        public_id=public_id,
        actor_user_id=actor_user_id,
        request_id=request_id,
        ip_address=ip_address,
        effective_date=effective_date,
        expiration_date=expiration_date,
    )
    msa = await _msa_row(conn, public_id)
    if str(msa["status"]) in {"ACTIVE", "EXPIRED", "TERMINATED"}:
        await conn.execute(
            text("UPDATE public.msas SET status = 'UNDER_REVIEW' WHERE id = :rid"),
            {"rid": msa["id"]},
        )
    return await get_msa(conn, company_id=company_id, public_id=public_id)
