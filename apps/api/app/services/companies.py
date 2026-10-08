"""Company, membership and role services.

Business rules that must not live in a route handler:
  * a user holds exactly one membership per company
  * a company always retains at least one active SUPER_ADMIN
  * deleting a company is an archive, never a hard delete
  * the number of companies a user belongs to is never disclosed
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.errors import (
    BusinessRuleViolationError,
    CompanyContextError,
    PermissionDeniedError,
    ResourceNotFoundError,
)
from app.core.logging import get_logger
from app.services import audit

logger = get_logger(__name__)


# ------------------------------------------------------------------- companies
async def list_my_companies(conn: AsyncConnection, user_id: uuid.UUID) -> list[dict[str, Any]]:
    """Companies the user is an active member of.

    Only the company, the caller's role and a public id are returned. Membership
    counts, revenue and any other tenant's details are not exposed.
    """
    rows = (
        (
            await conn.execute(
                text(
                    """
                SELECT c.public_id, c.display_name, c.legal_name, c.status,
                       c.default_currency, c.verification_state, c.created_at,
                       r.key AS role_key, r.name AS role_name
                  FROM public.company_memberships m
                  JOIN public.companies c ON c.id = m.company_id
                  JOIN public.company_roles r ON r.id = m.role_id
                 WHERE m.user_id = :user_id
                   AND m.status = 'ACTIVE'
                   AND c.deleted_at IS NULL
                 ORDER BY c.display_name
                """
                ),
                {"user_id": user_id},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def get_company(conn: AsyncConnection, company_id: uuid.UUID) -> dict[str, Any]:
    row = (
        (
            await conn.execute(
                text(
                    """
                SELECT public_id, legal_name, display_name, dba, country_code,
                       status, default_currency, verification_state, created_at,
                       fiscal_year_start
                  FROM public.companies
                 WHERE id = :id AND deleted_at IS NULL
                """
                ),
                {"id": company_id},
            )
        )
        .mappings()
        .first()
    )

    if row is None:
        raise ResourceNotFoundError("Company not found.")
    return dict(row)


async def create_company(
    conn: AsyncConnection,
    *,
    founder_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Create a company and make the founder its SUPER_ADMIN.

    The row, the role templates, the permission grants and the membership are all
    written in one transaction: a company can never exist without an owner who
    can administer it.
    """
    if not payload.get("w9_document_public_id"):
        # W-9 is mandatory. Enforced here as well as in the upload flow so a
        # direct API call cannot bypass the document pipeline.
        raise BusinessRuleViolationError(
            "A W-9 document must be uploaded and processed before the company can be created.",
            details={"required_document_type": "W9"},
        )

    # The W-9 must be the founder's own unclaimed intake upload: a document
    # belonging to another tenant (or an already-claimed one) is
    # indistinguishable from a missing one, so it cannot be borrowed.
    w9_id = (
        (
            await conn.execute(
                text(
                    """
                SELECT d.id::text AS id
                  FROM public.documents d
                 WHERE d.public_id = :pid
                   AND d.doc_type = 'W9'
                   AND d.company_id IS NULL
                   AND d.owner_user_id = :founder
                   AND d.deleted_at IS NULL
                """
                ),
                {"pid": payload["w9_document_public_id"], "founder": founder_user_id},
            )
        )
        .mappings()
        .first()
    )

    if w9_id is None:
        raise ResourceNotFoundError("The referenced W-9 document was not found.")

    row = (
        (
            await conn.execute(
                text(
                    """
                INSERT INTO public.companies
                  (legal_name, display_name, dba, country_code, address_line1,
                   address_line2, city, region, postal_code, default_currency,
                   w9_document_id, created_by)
                VALUES
                  (:legal_name, :display_name, :dba, :country_code, :address_line1,
                   :address_line2, :city, :region, :postal_code, :default_currency,
                   CAST(:w9_id AS uuid), :created_by)
                RETURNING id::text, public_id, created_at
                """
                ),
                {
                    "legal_name": payload["legal_name"],
                    "display_name": payload["display_name"],
                    "dba": payload.get("dba"),
                    "country_code": payload["country_code"],
                    "address_line1": payload.get("address_line1"),
                    "address_line2": payload.get("address_line2"),
                    "city": payload.get("city"),
                    "region": payload.get("region"),
                    "postal_code": payload.get("postal_code"),
                    "default_currency": payload.get("default_currency", "USD"),
                    "w9_id": w9_id["id"],
                    "created_by": founder_user_id,
                },
            )
        )
        .mappings()
        .first()
    )

    company_id = uuid.UUID(str(row["id"]))

    # Claim the intake W-9 into the new company in the same transaction.
    await conn.execute(
        text("UPDATE public.documents SET company_id = :cid WHERE id = CAST(:w9 AS uuid)"),
        {"cid": company_id, "w9": w9_id["id"]},
    )

    # Copies the role templates and grants the founder SUPER_ADMIN.
    await conn.execute(
        text("SELECT app.bootstrap_company_roles(:cid, :uid)"),
        {"cid": company_id, "uid": founder_user_id},
    )

    await conn.execute(
        text(
            """
            UPDATE public.companies
               SET settings = settings || jsonb_build_object(
                       -- :uid::text would not parse as a bind param; CAST instead.
                       'onboarding', jsonb_build_object(
                           'founder_user_id', CAST(:uid AS text)))
             WHERE id = :cid
            """
        ),
        {"cid": company_id, "uid": founder_user_id},
    )

    await audit.record(
        conn,
        action="company.created",
        resource_type="company",
        resource_id=company_id,
        resource_public_id=str(row["public_id"]),
        company_id=company_id,
        actor_user_id=founder_user_id,
        new_values={
            "legal_name": payload["legal_name"],
            "display_name": payload["display_name"],
            "country_code": payload["country_code"],
        },
        reason="company registration",
        request_id=request_id,
        ip_address=ip_address,
    )

    logger.info("company_created", company_id=str(company_id))
    company = await get_company(conn, company_id)
    company["my_role_keys"] = ["SUPER_ADMIN"]
    return company


async def update_company(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    changes: dict[str, Any],
) -> dict[str, Any]:
    """Update company profile fields. Never the tax identifier or status."""
    allowed = {
        "legal_name",
        "display_name",
        "dba",
        "country_code",
        "address_line1",
        "address_line2",
        "city",
        "region",
        "postal_code",
        "default_currency",
    }
    payload = {k: v for k, v in changes.items() if k in allowed and v is not None}
    if not payload:
        return await get_company(conn, company_id)

    before = await get_company(conn, company_id)

    assignments = ", ".join(f"{k} = :{k}" for k in payload)
    await conn.execute(
        text(f"UPDATE public.companies SET {assignments} WHERE id = :id"),  # noqa: S608 - allowlisted
        {**payload, "id": company_id},
    )

    await audit.record(
        conn,
        action="company.updated",
        resource_type="company",
        resource_id=company_id,
        resource_public_id=str(before["public_id"]),
        company_id=company_id,
        actor_user_id=actor_user_id,
        old_values=before,
        new_values=payload,
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_company(conn, company_id)


async def update_company_settings(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    request_id: str,
    settings: dict[str, Any],
) -> dict[str, Any]:
    """Merge company policy settings, recording the previous state.

    Policy lives in data, not code: invoice terms, timesheet locks, segregation of
    duties and auto-reconcile thresholds are all edited here.
    """
    row = (
        (
            await conn.execute(
                text("SELECT public_id, settings FROM public.companies WHERE id = :id"),
                {"id": company_id},
            )
        )
        .mappings()
        .first()
    )

    if row is None:
        raise ResourceNotFoundError("Company not found.")

    merged = {**(row["settings"] or {}), **settings}

    await conn.execute(
        text(
            """
            UPDATE public.companies
               SET settings = CAST(:settings AS jsonb)
             WHERE id = :id
            """
        ),
        {"settings": _json(merged), "id": company_id},
    )

    await audit.record(
        conn,
        action="company.settings_updated",
        resource_type="company",
        resource_id=company_id,
        resource_public_id=str(row["public_id"]),
        company_id=company_id,
        actor_user_id=actor_user_id,
        old_values={"settings": row["settings"]},
        new_values={"settings": merged},
        request_id=request_id,
    )

    await conn.execute(
        text(
            """
            INSERT INTO public.company_settings_history
              (company_id, changed_by, old_settings, new_settings, changed_fields)
            VALUES (:cid, :uid, CAST(:old AS jsonb), CAST(:new AS jsonb), CAST(:fields AS text[]))
            """
        ),
        {
            "cid": company_id,
            "uid": actor_user_id,
            "old": _json(row["settings"] or {}),
            "new": _json(merged),
            "fields": sorted(settings.keys()),
        },
    )
    return merged


async def archive_company(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    request_id: str,
    reason: str,
) -> None:
    """Soft-delete a company.

    A hard delete would cascade into contracts, invoices and payments, which are
    legal and financial records. Archiving preserves the audit trail.
    """
    row = (
        (
            await conn.execute(
                text(
                    "SELECT public_id FROM public.companies WHERE id = :id AND deleted_at IS NULL"
                ),
                {"id": company_id},
            )
        )
        .mappings()
        .first()
    )

    if row is None:
        raise ResourceNotFoundError("Company not found.")

    await conn.execute(
        text(
            """
            UPDATE public.companies
               SET deleted_at = now(), status = 'CLOSED'
             WHERE id = :id
            """
        ),
        {"id": company_id},
    )

    await audit.record(
        conn,
        action="company.archived",
        resource_type="company",
        resource_id=company_id,
        resource_public_id=str(row["public_id"]),
        company_id=company_id,
        actor_user_id=actor_user_id,
        reason=reason,
        request_id=request_id,
    )


# ------------------------------------------------------------------ memberships
async def list_members(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    include_rate: bool,
) -> list[dict[str, Any]]:
    """Company members. The internal hourly rate is opt-in by permission."""
    columns = (
        "m.hourly_rate::text AS hourly_rate, m.currency"
        if include_rate
        else "NULL::text AS hourly_rate, NULL::text AS currency"
    )
    rows = (
        (
            await conn.execute(
                text(
                    f"""
                SELECT m.public_id, m.status, m.job_title, m.department,
                       m.start_date, m.joined_at, r.key AS role_key, r.name AS role_name,
                       u.public_id AS user_public_id, u.first_name, u.last_name,
                       u.avatar_url, p.headline, {columns}
                  FROM public.company_memberships m
                  JOIN public.company_roles r ON r.id = m.role_id
                  JOIN public.users u ON u.id = m.user_id
                  LEFT JOIN public.user_profiles p ON p.user_id = u.id
                 WHERE m.company_id = :cid
                 ORDER BY m.joined_at NULLS LAST, u.first_name
                """  # noqa: S608 - the rate columns are chosen from a literal
                ),
                {"cid": company_id},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def change_member_role(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    member_public_id: str,
    new_role_key: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """Move a member to a different company role.

    Guarded against removing the last active SUPER_ADMIN by a database trigger,
    which fires regardless of which code path performs the update.
    """
    role = (
        (
            await conn.execute(
                text(
                    """
                SELECT r.id::text AS id, r.public_id, r.key
                  FROM public.company_roles r
                 WHERE r.company_id = :cid AND r.key = :key
                """
                ),
                {"cid": company_id, "key": new_role_key},
            )
        )
        .mappings()
        .first()
    )

    if role is None:
        raise ResourceNotFoundError("Role not found in this company.")

    member = (
        (
            await conn.execute(
                text(
                    """
                SELECT m.id::text AS id, m.public_id, r.key AS old_role_key
                  FROM public.company_memberships m
                  JOIN public.company_roles r ON r.id = m.role_id
                 WHERE m.company_id = :cid AND m.public_id = :pid
                """
                ),
                {"cid": company_id, "pid": member_public_id},
            )
        )
        .mappings()
        .first()
    )

    if member is None:
        raise ResourceNotFoundError("Member not found.")

    try:
        await conn.execute(
            text(
                """
                UPDATE public.company_memberships
                   SET role_id = CAST(:role_id AS uuid)
                 WHERE id = CAST(:mid AS uuid)
                """
            ),
            {"role_id": role["id"], "mid": member["id"]},
        )
    except Exception as exc:
        if "SUPER_ADMIN" in str(exc):
            raise BusinessRuleViolationError(
                "A company must always retain at least one active Super Admin."
            ) from exc
        raise

    await audit.record(
        conn,
        action="membership.role_changed",
        resource_type="company_membership",
        resource_id=uuid.UUID(str(member["id"])),
        resource_public_id=str(member["public_id"]),
        company_id=company_id,
        actor_user_id=actor_user_id,
        old_values={"role_key": member["old_role_key"]},
        new_values={"role_key": new_role_key},
        reason=reason,
        request_id=request_id,
    )
    return {"public_id": member_public_id, "role_key": new_role_key}


async def deactivate_member(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    member_public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    reason: str | None = None,
) -> None:
    """Deactivate rather than delete: history must survive the relationship."""
    member = (
        (
            await conn.execute(
                text(
                    """
                SELECT m.id::text AS id, m.public_id
                  FROM public.company_memberships m
                 WHERE m.company_id = :cid AND m.public_id = :pid
                """
                ),
                {"cid": company_id, "pid": member_public_id},
            )
        )
        .mappings()
        .first()
    )

    if member is None:
        raise ResourceNotFoundError("Member not found.")

    try:
        await conn.execute(
            text(
                """
                UPDATE public.company_memberships
                   SET status = 'DEACTIVATED', end_date = COALESCE(end_date, current_date)
                 WHERE id = CAST(:mid AS uuid)
                """
            ),
            {"mid": member["id"]},
        )
    except Exception as exc:
        if "SUPER_ADMIN" in str(exc):
            raise BusinessRuleViolationError(
                "A company must always retain at least one active Super Admin."
            ) from exc
        raise

    await audit.record(
        conn,
        action="membership.deactivated",
        resource_type="company_membership",
        resource_id=uuid.UUID(str(member["id"])),
        resource_public_id=str(member["public_id"]),
        company_id=company_id,
        actor_user_id=actor_user_id,
        new_values={"status": "DEACTIVATED"},
        reason=reason,
        request_id=request_id,
    )


async def invite_member(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    email: str,
    role_key: str,
    job_title: str | None,
    message: str | None,
    actor_user_id: uuid.UUID,
    request_id: str,
) -> dict[str, Any]:
    """Create a single-use invitation.

    Only a sha256 of the token is stored, so a database leak does not yield
    working invitations.
    """
    import hashlib
    import secrets

    role = (
        (
            await conn.execute(
                text(
                    """
                SELECT r.id::text AS id, r.public_id, r.key
                  FROM public.company_roles r
                 WHERE r.company_id = :cid AND r.key = :key
                """
                ),
                {"cid": company_id, "key": role_key},
            )
        )
        .mappings()
        .first()
    )

    if role is None:
        raise ResourceNotFoundError("Role not found in this company.")

    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    row = (
        (
            await conn.execute(
                text(
                    """
                INSERT INTO public.company_invitations
                  (company_id, email, role_id, invited_by, token_hash, job_title, message)
                VALUES (:cid, :email, CAST(:rid AS uuid), :uid, :hash, :title, :msg)
                ON CONFLICT (company_id, email, status) DO UPDATE
                    SET role_id = EXCLUDED.role_id,
                        token_hash = EXCLUDED.token_hash,
                        job_title = EXCLUDED.job_title,
                        message = EXCLUDED.message,
                        expires_at = now() + interval '7 days'
                RETURNING public_id, expires_at
                """
                ),
                {
                    "cid": company_id,
                    "email": email,
                    "rid": role["id"],
                    "uid": actor_user_id,
                    "hash": token_hash,
                    "title": job_title,
                    "msg": message,
                },
            )
        )
        .mappings()
        .first()
    )

    await audit.record(
        conn,
        action="invitation.sent",
        resource_type="company_invitation",
        resource_id=uuid.UUID(str(row["public_id"])),
        company_id=company_id,
        actor_user_id=actor_user_id,
        new_values={"email": email, "role_key": role_key},
        request_id=request_id,
    )

    # The raw token is returned exactly once, to the inviter.
    return {
        "public_id": str(row["public_id"]),
        "invitation_token": raw_token,
        "expires_at": row["expires_at"],
    }


async def accept_invitation(
    conn: AsyncConnection,
    *,
    token: str,
    user_id: uuid.UUID,
    request_id: str,
) -> dict[str, Any]:
    """Accept an invitation, creating the membership in the same transaction."""
    import hashlib

    token_hash = hashlib.sha256(token.encode()).hexdigest()

    row = (
        (
            await conn.execute(
                text(
                    """
                SELECT i.id::text AS id, i.public_id, i.company_id::text AS company_id,
                       i.email::text AS email, i.role_id::text AS role_id,
                       i.expires_at, c.public_id AS company_public_id
                  FROM public.company_invitations i
                  JOIN public.companies c ON c.id = i.company_id
                 WHERE i.token_hash = :hash
                   AND i.status = 'PENDING'
                """
                ),
                {"hash": token_hash},
            )
        )
        .mappings()
        .first()
    )

    if row is None:
        raise ResourceNotFoundError("This invitation is not valid.")

    import datetime as dt

    if row["expires_at"] < dt.datetime.now(dt.UTC):
        await conn.execute(
            text("UPDATE public.company_invitations SET status = 'EXPIRED' WHERE id = :id"),
            {"id": row["id"]},
        )
        raise BusinessRuleViolationError("This invitation has expired.")

    email_now = (
        (
            await conn.execute(
                text("SELECT email::text AS email FROM public.users WHERE id = :uid"),
                {"uid": user_id},
            )
        )
        .mappings()
        .first()
    )

    if (email_now or {}).get("email", "").lower() != str(row["email"]).lower():
        raise PermissionDeniedError("This invitation was issued to a different email address.")

    await conn.execute(
        text(
            """
            INSERT INTO public.company_memberships
              (company_id, user_id, role_id, status, joined_at, job_title)
            VALUES (CAST(:cid AS uuid), :uid, CAST(:rid AS uuid), 'ACTIVE', now(),
                    (SELECT job_title FROM public.company_invitations WHERE id = :id))
            ON CONFLICT (company_id, user_id) DO UPDATE
               SET role_id = EXCLUDED.role_id, status = 'ACTIVE', joined_at = now()
            """
        ),
        {"cid": row["company_id"], "uid": user_id, "rid": row["role_id"], "id": row["id"]},
    )

    await conn.execute(
        text(
            "UPDATE public.company_invitations SET status = 'ACCEPTED', accepted_at = now(), "
            "accepted_user_id = :uid WHERE id = :id"
        ),
        {"uid": user_id, "id": row["id"]},
    )

    await audit.record(
        conn,
        action="invitation.accepted",
        resource_type="company_invitation",
        resource_id=uuid.UUID(str(row["id"])),
        resource_public_id=str(row["public_id"]),
        company_id=uuid.UUID(str(row["company_id"])),
        actor_user_id=user_id,
        request_id=request_id,
    )

    return {"company_public_id": row["company_public_id"]}


# ------------------------------------------------------------------------ roles
async def list_roles(conn: AsyncConnection, company_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        (
            await conn.execute(
                text(
                    """
                SELECT r.public_id, r.key, r.name, r.description, r.is_system,
                       r.is_assignable,
                       (SELECT count(*) FROM public.company_memberships m
                         WHERE m.role_id = r.id AND m.status = 'ACTIVE')::int AS member_count,
                       COALESCE(array_agg(rp.permission_key ORDER BY rp.permission_key)
                                FILTER (WHERE rp.permission_key IS NOT NULL), '{}') AS permissions
                  FROM public.company_roles r
                  LEFT JOIN public.role_permissions rp ON rp.role_id = r.id
                 WHERE r.company_id = :cid AND r.deleted_at IS NULL
                 GROUP BY r.id
                 ORDER BY r.is_system DESC, r.name
                """
                ),
                {"cid": company_id},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def update_role_permissions(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    role_public_id: str,
    permission_keys: list[str],
    actor_user_id: uuid.UUID,
    request_id: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """Replace a role's permission set.

    Unknown keys are rejected rather than ignored: silently dropping a
    misspelled permission would leave the role more permissive than intended.
    """
    role = (
        (
            await conn.execute(
                text(
                    "SELECT id::text, public_id, key, is_system FROM public.company_roles "
                    "WHERE company_id = :cid AND public_id = :pid AND deleted_at IS NULL"
                ),
                {"cid": company_id, "pid": role_public_id},
            )
        )
        .mappings()
        .first()
    )

    if role is None:
        raise ResourceNotFoundError("Role not found.")

    if role["is_system"] and role["key"] == "SUPER_ADMIN":
        # Granting the wildcard back is required for SUPER_ADMIN to work, so it
        # is not editable through this path.
        raise BusinessRuleViolationError("The Super Admin role's permissions cannot be edited.")

    known = (
        (
            await conn.execute(
                text("SELECT key FROM public.permissions WHERE key = ANY(:keys)"),
                {"keys": permission_keys},
            )
        )
        .scalars()
        .all()
    )
    unknown = sorted(set(permission_keys) - set(known))
    if unknown:
        raise BusinessRuleViolationError(
            "Unknown permission keys were supplied.", details={"unknown": unknown}
        )

    before = (
        (
            await conn.execute(
                text(
                    """
                    SELECT rp.permission_key
                      FROM public.role_permissions rp
                     WHERE rp.role_id = CAST(:rid AS uuid)
                    """
                ),
                {"rid": role["id"]},
            )
        )
        .scalars()
        .all()
    )

    await conn.execute(
        text("DELETE FROM public.role_permissions WHERE role_id = CAST(:rid AS uuid)"),
        {"rid": role["id"]},
    )

    if permission_keys:
        await conn.execute(
            text(
                """
                INSERT INTO public.role_permissions (role_id, permission_key, granted_by)
                SELECT CAST(:rid AS uuid), k, :uid FROM unnest(:keys) AS k
                ON CONFLICT DO NOTHING
                """
            ),
            {"rid": role["id"], "keys": permission_keys, "uid": actor_user_id},
        )

    await audit.record(
        conn,
        action="role.permissions_replaced",
        resource_type="company_role",
        resource_id=uuid.UUID(str(role["id"])),
        resource_public_id=str(role["public_id"]),
        company_id=company_id,
        actor_user_id=actor_user_id,
        old_values={"permissions": sorted(before)},
        new_values={"permissions": sorted(permission_keys)},
        reason=reason,
        request_id=request_id,
    )
    return {"public_id": role_public_id, "permissions": sorted(permission_keys)}


async def create_role(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    name: str,
    description: str,
    permission_keys: list[str],
    actor_user_id: uuid.UUID,
    request_id: str,
) -> dict[str, Any]:
    """Create a custom company role from the permission catalogue."""
    key = _slugify(name)
    if len(key) < 2:
        raise BusinessRuleViolationError("Role name must contain letters or digits.")

    exists = (
        (
            await conn.execute(
                text("SELECT 1 FROM public.company_roles WHERE company_id = :cid AND key = :key"),
                {"cid": company_id, "key": key},
            )
        )
        .mappings()
        .first()
    )
    if exists:
        raise BusinessRuleViolationError("A role with this name already exists.")

    known = (
        (
            await conn.execute(
                text("SELECT key FROM public.permissions WHERE key = ANY(:keys)"),
                {"keys": permission_keys},
            )
        )
        .scalars()
        .all()
    )
    unknown = sorted(set(permission_keys) - set(known))
    if unknown:
        raise BusinessRuleViolationError(
            "Unknown permission keys were supplied.", details={"unknown": unknown}
        )

    row = (
        (
            await conn.execute(
                text(
                    """
                INSERT INTO public.company_roles
                  (company_id, key, name, description, is_system, created_by)
                VALUES (:cid, :key, :name, :desc, false, :uid)
                RETURNING id::text, public_id
                """
                ),
                {
                    "cid": company_id,
                    "key": key,
                    "name": name,
                    "desc": description,
                    "uid": actor_user_id,
                },
            )
        )
        .mappings()
        .first()
    )

    if permission_keys:
        await conn.execute(
            text(
                """
                INSERT INTO public.role_permissions (role_id, permission_key, granted_by)
                SELECT CAST(:rid AS uuid), k, :uid FROM unnest(:keys) AS k
                """
            ),
            {"rid": row["id"], "keys": permission_keys, "uid": actor_user_id},
        )

    await audit.record(
        conn,
        action="role.created",
        resource_type="company_role",
        resource_id=uuid.UUID(str(row["id"])),
        resource_public_id=str(row["public_id"]),
        company_id=company_id,
        actor_user_id=actor_user_id,
        new_values={"name": name, "key": key, "permissions": sorted(permission_keys)},
        request_id=request_id,
    )
    return {"public_id": str(row["public_id"]), "key": key, "name": name}


# -------------------------------------------------------------------- helpers
def _slugify(value: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_").upper()[:40]


def _json(value: Any) -> str:
    import json

    return json.dumps(value, default=str)


def ensure_company(ctx_company_id: uuid.UUID | None) -> uuid.UUID:
    if ctx_company_id is None:
        raise CompanyContextError()
    return ctx_company_id
