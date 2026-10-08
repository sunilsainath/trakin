"""Identity services: profile reads, updates, and the privacy filter.

The privacy filter is the important part. A profile response is assembled field by
field, each field passing through its visibility rule, so a field added to the
database later cannot leak by being forgotten in a projection.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.errors import ResourceNotFoundError, ValidationError
from app.core.logging import get_logger
from app.services import audit

logger = get_logger(__name__)

# Fields that require an explicit privacy decision. Everything else is public
# profile data and follows the profile-level visibility.
PRIVACY_GATED_FIELDS: dict[str, str] = {
    "email": "contact.email",
    "phone_e164": "contact.phone",
    "education": "education",
    "experience": "experience",
    "certifications": "certifications",
    "location": "location",
    "rates": "rates",
    "sensitive": "sensitive",
}


async def get_me(conn: AsyncConnection, user_id: uuid.UUID) -> dict[str, Any]:
    row = (
        (
            await conn.execute(
                text(
                    """
                SELECT u.public_id, u.email::text AS email, u.email_verified_at IS NOT NULL
                         AS email_verified, u.first_name, u.last_name, u.phone_e164,
                       u.country_code, u.avatar_url, u.status, u.created_at,
                       u.onboarding_completed_at, u.default_currency, u.default_visibility,
                       EXISTS (SELECT 1 FROM public.company_memberships m
                                WHERE m.user_id = u.id AND m.status = 'ACTIVE')
                         AS has_company,
                       p.headline, p.bio, p.location_city, p.location_country, p.timezone,
                       u.notification_preferences, u.ai_settings, u.privacy_settings,
                       u.security_settings
                  FROM public.users u
                  LEFT JOIN public.user_profiles p ON p.user_id = u.id
                 WHERE u.id = :uid
                """
                ),
                {"uid": user_id},
            )
        )
        .mappings()
        .first()
    )

    if row is None:
        raise ResourceNotFoundError("User not found.")

    data = dict(row)
    data["onboarding_completed"] = data.pop("onboarding_completed_at") is not None
    data["email_verified"] = bool(data["email_verified"])
    data["settings"] = {
        "notifications": data.pop("notification_preferences", {}) or {},
        "ai": data.pop("ai_settings", {}) or {},
        "privacy": data.pop("privacy_settings", {}) or {},
        "security": data.pop("security_settings", {}) or {},
    }
    data.pop("default_currency", None)
    data.pop("default_visibility", None)
    return data


async def update_me(
    conn: AsyncConnection,
    *,
    user_id: uuid.UUID,
    changes: dict[str, Any],
    request_id: str,
    ip_address: str | None,
) -> dict[str, Any]:
    """Update own profile.

    Status, verification and public_id are never writable: a user cannot promote
    their own account to ACTIVE or change their identifier.
    """
    user_fields = {
        "first_name",
        "last_name",
        "phone_e164",
        "country_code",
        "avatar_url",
        "default_currency",
        "default_visibility",
    }
    profile_fields = {"headline", "bio", "location_city", "location_country", "timezone"}

    user_payload = {k: v for k, v in changes.items() if k in user_fields and v is not None}
    profile_payload = {k: v for k, v in changes.items() if k in profile_fields and v is not None}

    before = await get_me(conn, user_id)

    if user_payload:
        assignments = ", ".join(f"{k} = :{k}" for k in user_payload)
        await conn.execute(
            text(f"UPDATE public.users SET {assignments} WHERE id = :uid"),  # noqa: S608 - allowlisted
            {**user_payload, "uid": user_id},
        )

    if profile_payload:
        assignments = ", ".join(f"{k} = :{k}" for k in profile_payload)
        await conn.execute(
            text(
                f"INSERT INTO public.user_profiles (user_id) VALUES (:uid) "  # noqa: S608
                f"ON CONFLICT (user_id) DO UPDATE SET {assignments}"
            ),
            {**profile_payload, "uid": user_id},
        )

    if user_payload or profile_payload:
        await audit.record(
            conn,
            action="profile.updated",
            resource_type="user",
            resource_id=user_id,
            resource_public_id=str(before["public_id"]),
            actor_user_id=user_id,
            old_values=before,
            new_values={**user_payload, **profile_payload},
            request_id=request_id,
            ip_address=ip_address,
        )

    return await get_me(conn, user_id)


async def get_profile(
    conn: AsyncConnection,
    *,
    viewer_id: uuid.UUID,
    target_user_id: uuid.UUID,
) -> dict[str, Any]:
    """A profile as `viewer_id` is allowed to see it.

    Field-level privacy is applied here, and a field the viewer may not see is
    omitted rather than nulled, so its absence is not itself informative.
    """
    visibility = await _effective_visibility(conn, viewer_id, target_user_id)
    if visibility is None:
        raise ResourceNotFoundError("Profile not found.")

    row = (
        (
            await conn.execute(
                text(
                    """
                SELECT u.public_id, u.first_name, u.last_name, u.avatar_url,
                       u.created_at,
                       p.headline, p.bio, p.location_city, p.location_country,
                       p.timezone, p.profile_visibility, p.years_experience,
                       p.availability_status, p.linkedin_url, p.website_url
                  FROM public.users u
                  JOIN public.user_profiles p ON p.user_id = u.id
                 WHERE u.id = :uid AND u.deleted_at IS NULL AND u.status = 'ACTIVE'
                """
                ),
                {"uid": target_user_id},
            )
        )
        .mappings()
        .first()
    )

    if row is None:
        raise ResourceNotFoundError("Profile not found.")

    data = dict(row)
    data["display_name"] = f"{row['first_name']} {row['last_name']}".strip()

    overrides = await _privacy_overrides(conn, target_user_id)

    def allowed(field: str) -> bool:
        path = PRIVACY_GATED_FIELDS.get(field)
        if path and path in overrides:
            return overrides[path] != "PRIVATE" and visibility != "PRIVATE"
        if path and path in {"email", "phone_e164", "rates", "sensitive"}:
            return viewer_id == target_user_id
        if path == "location":
            return visibility in {"PUBLIC", "CONNECTIONS"}
        return visibility in {"PUBLIC", "CONNECTIONS"}

    for gated in ("linkedin_url", "website_url", "years_experience", "availability_status"):
        if gated in data and not allowed(gated):
            data.pop(gated)

    data["skills"] = (
        (
            await conn.execute(
                text(
                    """
                SELECT s.name
                  FROM public.user_skills us
                  JOIN public.skills s ON s.id = us.skill_id
                 WHERE us.user_id = :uid AND us.visible
                 ORDER BY us.proficiency DESC, s.name
                 LIMIT 50
                """
                ),
                {"uid": target_user_id},
            )
        )
        .scalars()
        .all()
    )

    data["education"] = (
        (
            await conn.execute(
                text(
                    """
                SELECT institution, degree, field_of_study, start_date, end_date,
                       grade, description
                  FROM public.user_educations
                 WHERE user_id = :uid AND visible
                 ORDER BY end_date DESC NULLS LAST, start_date DESC NULLS LAST
                """
                ),
                {"uid": target_user_id},
            )
        )
        .mappings()
        .all()
    )
    data["education"] = [dict(r) for r in data["education"]]

    data["experience"] = (
        (
            await conn.execute(
                text(
                    """
                    SELECT company_name, title, employment_type, location,
                           description, start_date, end_date, is_current
                      FROM public.user_experiences
                     WHERE user_id = :uid AND visible
                     ORDER BY is_current DESC, end_date DESC NULLS LAST,
                              start_date DESC
                    """
                ),
                {"uid": target_user_id},
            )
        )
        .mappings()
        .all()
    )
    data["experience"] = [dict(r) for r in data["experience"]]

    data["connection_state"], data["mutual_connections"] = await _connection_state(
        conn, viewer_id, target_user_id
    )

    # Contact details and sensitive identifiers are owner-only and masked.
    data["email"] = None
    data["phone_e164"] = None
    if viewer_id == target_user_id:
        data["email"] = (
            await conn.execute(
                text("SELECT email::text AS email FROM public.users WHERE id = :uid"),
                {"uid": target_user_id},
            )
        ).scalar()
        data["sensitive"] = await _masked_sensitive(conn, target_user_id)
    else:
        data["sensitive"] = []

    return data


async def _effective_visibility(
    conn: AsyncConnection, viewer_id: uuid.UUID, target_id: uuid.UUID
) -> str | None:
    """Resolve profile visibility, or None when the viewer may not see it.

    Delegates to the SQL predicate so the API and the RLS policies agree. The
    predicate is session-scoped (`app.current_user_id()` is the viewer), which
    is why it is called with the target only: the request path has already set
    the caller's identity via `set_identity`.
    """
    result = await conn.execute(
        text("SELECT app.can_view_profile(:target) AS allowed"),
        {"target": target_id},
    )
    if not result.scalar():
        return None

    if viewer_id == target_id:
        return "PRIVATE"

    connected = await conn.execute(
        text("SELECT app.can_view_connected(:target) AS connected"),
        {"target": target_id},
    )
    if connected.scalar():
        return "CONNECTIONS"

    row = (
        (
            await conn.execute(
                text("SELECT profile_visibility FROM public.user_profiles WHERE user_id = :uid"),
                {"uid": target_id},
            )
        )
        .mappings()
        .first()
    )
    return str(row["profile_visibility"]) if row else "PUBLIC"


async def _privacy_overrides(conn: AsyncConnection, user_id: uuid.UUID) -> dict[str, str]:
    rows = (
        (
            await conn.execute(
                text(
                    """
                    SELECT field_path, visibility
                      FROM public.user_privacy
                     WHERE user_id = :uid
                    """
                ),
                {"uid": user_id},
            )
        )
        .mappings()
        .all()
    )
    return {r["field_path"]: r["visibility"] for r in rows}


async def _masked_sensitive(conn: AsyncConnection, user_id: uuid.UUID) -> list[dict[str, str]]:
    """Masked sensitive values. The full identifier never leaves the database."""
    row = (
        (
            await conn.execute(
                text(
                    """
                SELECT ssn_last4, tax_id_last4, tax_id_type, verification_state
                  FROM public.user_sensitive
                 WHERE user_id = :uid
                """
                ),
                {"uid": user_id},
            )
        )
        .mappings()
        .first()
    )

    if row is None:
        return []

    out: list[dict[str, str]] = []
    if row["ssn_last4"]:
        out.append(
            {
                "field": "ssn",
                "masked": f"••••{row['ssn_last4']}",
                "verification_state": str(row["verification_state"]),
            }
        )
    if row["tax_id_last4"]:
        label = row["tax_id_type"] or "tax_id"
        out.append(
            {
                "field": label.lower(),
                "masked": f"••••{row['tax_id_last4']}",
                "verification_state": str(row["verification_state"]),
            }
        )
    return out


async def _connection_state(
    conn: AsyncConnection, viewer_id: uuid.UUID, target_id: uuid.UUID
) -> tuple[str, int]:
    if viewer_id == target_id:
        return "NONE", 0

    row = (
        (
            await conn.execute(
                text(
                    """
                SELECT
                  (SELECT status FROM public.connections
                    WHERE user_low = LEAST(:a, :b) AND user_high = GREATEST(:a, :b)
                   ) AS conn_status,
                  (SELECT mutual_count FROM public.connections
                    WHERE user_low = LEAST(:a, :b) AND user_high = GREATEST(:a, :b)
                   ) AS mutual
                """
                ),
                {"a": viewer_id, "b": target_id},
            )
        )
        .mappings()
        .first()
    )

    if row is None:
        req = (
            await conn.execute(
                text(
                    """
                    SELECT CASE
                             WHEN requester_id = :viewer THEN 'PENDING_SENT'
                             ELSE 'PENDING_RECEIVED'
                           END AS state
                      FROM public.connection_requests
                     WHERE status = 'PENDING'
                       AND ((requester_id = :viewer AND addressee_id = :target)
                         OR (requester_id = :target  AND addressee_id = :viewer))
                    """
                ),
                {"viewer": viewer_id, "target": target_id},
            )
        ).scalar()
        return (str(req) if req else "NONE"), 0

    if row["conn_status"] == "ACCEPTED":
        return "CONNECTED", int(row["mutual"] or 0)
    return "NONE", int(row["mutual"] or 0)


async def set_privacy(
    conn: AsyncConnection,
    *,
    user_id: uuid.UUID,
    field_visibility: dict[str, str],
    request_id: str,
) -> dict[str, str]:
    """Set per-field visibility for the caller's own profile."""
    for path, visibility in field_visibility.items():
        if visibility not in {"PUBLIC", "CONNECTIONS", "PRIVATE"}:
            raise ResourceNotFoundError(f"Invalid visibility for {path}.")
        await conn.execute(
            text(
                """
                INSERT INTO public.user_privacy (user_id, field_path, visibility)
                VALUES (:uid, :path, CAST(:vis AS public.visibility))
                ON CONFLICT (user_id, field_path) DO UPDATE SET visibility = EXCLUDED.visibility
                """
            ),
            {"uid": user_id, "path": path, "vis": visibility},
        )

    await audit.record(
        conn,
        action="profile.privacy_updated",
        resource_type="user",
        resource_id=user_id,
        actor_user_id=user_id,
        new_values={"privacy": field_visibility},
        request_id=request_id,
    )
    return field_visibility


async def get_notification_preferences(
    conn: AsyncConnection, *, user_id: uuid.UUID
) -> list[dict[str, Any]]:
    """The caller's per-category delivery channels."""
    rows = (
        (
            await conn.execute(
                text(
                    """
                    SELECT category, in_app, email, push
                      FROM platform.notification_preferences
                     WHERE user_id = :uid
                     ORDER BY category
                    """
                ),
                {"uid": user_id},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def set_notification_preferences(
    conn: AsyncConnection,
    *,
    user_id: uuid.UUID,
    preferences: list[dict[str, Any]],
    request_id: str,
) -> list[dict[str, Any]]:
    """Replace delivery channels per category (unknown categories rejected)."""
    from app.core.errors import ValidationError

    known = set(
        (
            await conn.execute(
                text("SELECT DISTINCT category FROM platform.notification_preferences")
            )
        ).scalars()
    )
    for pref in preferences:
        category = str(pref.get("category", "")).upper()
        if category not in known:
            raise ValidationError(
                f"Unknown notification category: {pref.get('category')}.",
                details={"category": pref.get("category")},
            )
        await conn.execute(
            text(
                """
                INSERT INTO platform.notification_preferences
                  (user_id, category, in_app, email, push)
                VALUES (:uid, :category, :in_app, :email, :push)
                ON CONFLICT (user_id, category) DO UPDATE SET
                  in_app = EXCLUDED.in_app,
                  email = EXCLUDED.email,
                  push = EXCLUDED.push
                """
            ),
            {
                "uid": user_id,
                "category": category,
                "in_app": bool(pref.get("in_app", True)),
                "email": bool(pref.get("email", True)),
                "push": bool(pref.get("push", True)),
            },
        )

    await audit.record(
        conn,
        action="profile.notifications_updated",
        resource_type="user",
        resource_id=user_id,
        actor_user_id=user_id,
        new_values={"categories": len(preferences)},
        request_id=request_id,
    )
    return await get_notification_preferences(conn, user_id=user_id)


# =============================================================================
# career: education, experience, skills, work authorization
# =============================================================================
# Everything here is own-profile only: the caller addresses rows by internal
# id, but every statement is scoped to the actor's user id, so one user can
# never read or mutate another's career history. Education and experience are
# professional data and ride along on the visible profile; visa status lives
# in the protected table and is never exposed except through the masked
# owner-only projection.

_EDU_UPDATABLE = frozenset(
    {
        "institution",
        "degree",
        "field_of_study",
        "start_date",
        "end_date",
        "grade",
        "description",
        "credential_id",
        "visible",
    }
)

_EXP_UPDATABLE = frozenset(
    {
        "company_name",
        "title",
        "employment_type",
        "location",
        "description",
        "start_date",
        "end_date",
        "is_current",
        "visible",
    }
)


async def _own_row(
    conn: AsyncConnection, table: str, row_id: uuid.UUID, user_id: uuid.UUID
) -> dict[str, Any]:
    """One career row belonging to the actor, or a 404 that reveals nothing."""
    if table not in {"user_educations", "user_experiences"}:
        raise ResourceNotFoundError("Unknown resource type.")
    row = (
        (
            await conn.execute(
                text(
                    f"SELECT * FROM public.{table} "  # noqa: S608 - allowlisted table
                    "WHERE id = :rid AND user_id = :uid"
                ),
                {"rid": row_id, "uid": user_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ResourceNotFoundError("Entry not found.")
    return dict(row)


async def list_education(conn: AsyncConnection, *, user_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        (
            await conn.execute(
                text(
                    """
                    SELECT id::text AS id, institution, degree, field_of_study,
                           start_date, end_date, grade, description, credential_id,
                           is_verified, visible, created_at, updated_at
                      FROM public.user_educations
                     WHERE user_id = :uid
                     ORDER BY end_date DESC NULLS LAST, start_date DESC NULLS LAST
                    """
                ),
                {"uid": user_id},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def add_education(
    conn: AsyncConnection,
    *,
    user_id: uuid.UUID,
    payload: dict[str, Any],
    request_id: str,
    ip_address: str | None,
) -> dict[str, Any]:
    row = (
        (
            await conn.execute(
                text(
                    """
                    INSERT INTO public.user_educations
                      (user_id, institution, degree, field_of_study, start_date,
                       end_date, grade, description, credential_id, visible)
                    VALUES (:uid, :institution, :degree, :field_of_study, :start_date,
                            :end_date, :grade, :description, :credential_id,
                            COALESCE(:visible, true))
                    RETURNING id
                    """
                ),
                {
                    "uid": user_id,
                    "institution": payload["institution"],
                    "degree": payload.get("degree"),
                    "field_of_study": payload.get("field_of_study"),
                    "start_date": payload.get("start_date"),
                    "end_date": payload.get("end_date"),
                    "grade": payload.get("grade"),
                    "description": payload.get("description"),
                    "credential_id": payload.get("credential_id"),
                    "visible": payload.get("visible", True),
                },
            )
        )
        .mappings()
        .first()
    )
    assert row is not None
    await audit.record(
        conn,
        action="profile.education_added",
        resource_type="user",
        resource_id=user_id,
        actor_user_id=user_id,
        new_values={"institution": payload["institution"]},
        request_id=request_id,
        ip_address=ip_address,
    )
    return await _own_row(conn, "user_educations", uuid.UUID(str(row["id"])), user_id)


async def update_education(
    conn: AsyncConnection,
    *,
    user_id: uuid.UUID,
    education_id: uuid.UUID,
    changes: dict[str, Any],
    request_id: str,
    ip_address: str | None,
) -> dict[str, Any]:
    before = await _own_row(conn, "user_educations", education_id, user_id)
    updates = {k: v for k, v in changes.items() if k in _EDU_UPDATABLE and v is not None}
    if updates:
        assignments = ", ".join(f"{col} = :{col}" for col in updates)
        await conn.execute(
            text(
                f"UPDATE public.user_educations SET {assignments} "  # noqa: S608 - allowlisted
                "WHERE id = :rid AND user_id = :uid"
            ),
            {**updates, "rid": education_id, "uid": user_id},
        )
        await audit.record(
            conn,
            action="profile.education_updated",
            resource_type="user",
            resource_id=user_id,
            actor_user_id=user_id,
            old_values={k: before.get(k) for k in updates},
            new_values=updates,
            request_id=request_id,
            ip_address=ip_address,
        )
    return await _own_row(conn, "user_educations", education_id, user_id)


async def delete_education(
    conn: AsyncConnection,
    *,
    user_id: uuid.UUID,
    education_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
) -> None:
    before = await _own_row(conn, "user_educations", education_id, user_id)
    await conn.execute(
        text("DELETE FROM public.user_educations WHERE id = :rid AND user_id = :uid"),
        {"rid": education_id, "uid": user_id},
    )
    await audit.record(
        conn,
        action="profile.education_removed",
        resource_type="user",
        resource_id=user_id,
        actor_user_id=user_id,
        old_values={"institution": before.get("institution")},
        request_id=request_id,
        ip_address=ip_address,
    )


async def list_experience(conn: AsyncConnection, *, user_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        (
            await conn.execute(
                text(
                    """
                    SELECT id::text AS id, company_name, title, employment_type,
                           location, description, start_date, end_date, is_current,
                           visible, created_at, updated_at
                      FROM public.user_experiences
                     WHERE user_id = :uid
                     ORDER BY is_current DESC, end_date DESC NULLS LAST, start_date DESC
                    """
                ),
                {"uid": user_id},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def add_experience(
    conn: AsyncConnection,
    *,
    user_id: uuid.UUID,
    payload: dict[str, Any],
    request_id: str,
    ip_address: str | None,
) -> dict[str, Any]:
    row = (
        (
            await conn.execute(
                text(
                    """
                    INSERT INTO public.user_experiences
                      (user_id, company_name, title, employment_type, location,
                       description, start_date, end_date, is_current, visible)
                    VALUES (:uid, :company_name, :title, :employment_type, :location,
                            :description, :start_date, :end_date,
                            COALESCE(:is_current, false), COALESCE(:visible, true))
                    RETURNING id
                    """
                ),
                {
                    "uid": user_id,
                    "company_name": payload["company_name"],
                    "title": payload["title"],
                    "employment_type": payload.get("employment_type"),
                    "location": payload.get("location"),
                    "description": payload.get("description"),
                    "start_date": payload["start_date"],
                    "end_date": payload.get("end_date"),
                    "is_current": payload.get("is_current", False),
                    "visible": payload.get("visible", True),
                },
            )
        )
        .mappings()
        .first()
    )
    assert row is not None
    await audit.record(
        conn,
        action="profile.experience_added",
        resource_type="user",
        resource_id=user_id,
        actor_user_id=user_id,
        new_values={"company_name": payload["company_name"], "title": payload["title"]},
        request_id=request_id,
        ip_address=ip_address,
    )
    return await _own_row(conn, "user_experiences", uuid.UUID(str(row["id"])), user_id)


async def update_experience(
    conn: AsyncConnection,
    *,
    user_id: uuid.UUID,
    experience_id: uuid.UUID,
    changes: dict[str, Any],
    request_id: str,
    ip_address: str | None,
) -> dict[str, Any]:
    before = await _own_row(conn, "user_experiences", experience_id, user_id)
    updates = {k: v for k, v in changes.items() if k in _EXP_UPDATABLE and v is not None}
    if updates:
        assignments = ", ".join(f"{col} = :{col}" for col in updates)
        await conn.execute(
            text(
                f"UPDATE public.user_experiences SET {assignments} "  # noqa: S608 - allowlisted
                "WHERE id = :rid AND user_id = :uid"
            ),
            {**updates, "rid": experience_id, "uid": user_id},
        )
        await audit.record(
            conn,
            action="profile.experience_updated",
            resource_type="user",
            resource_id=user_id,
            actor_user_id=user_id,
            old_values={k: before.get(k) for k in updates},
            new_values=updates,
            request_id=request_id,
            ip_address=ip_address,
        )
    return await _own_row(conn, "user_experiences", experience_id, user_id)


async def delete_experience(
    conn: AsyncConnection,
    *,
    user_id: uuid.UUID,
    experience_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
) -> None:
    before = await _own_row(conn, "user_experiences", experience_id, user_id)
    await conn.execute(
        text("DELETE FROM public.user_experiences WHERE id = :rid AND user_id = :uid"),
        {"rid": experience_id, "uid": user_id},
    )
    await audit.record(
        conn,
        action="profile.experience_removed",
        resource_type="user",
        resource_id=user_id,
        actor_user_id=user_id,
        old_values={"company_name": before.get("company_name"), "title": before.get("title")},
        request_id=request_id,
        ip_address=ip_address,
    )


async def list_my_skills(conn: AsyncConnection, *, user_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        (
            await conn.execute(
                text(
                    """
                    SELECT s.id::text AS skill_id, s.name, s.category, us.proficiency,
                           us.years_experience, us.visible
                      FROM public.user_skills us
                      JOIN public.skills s ON s.id = us.skill_id
                     WHERE us.user_id = :uid
                     ORDER BY us.proficiency DESC, s.name
                    """
                ),
                {"uid": user_id},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def add_skill(
    conn: AsyncConnection,
    *,
    user_id: uuid.UUID,
    payload: dict[str, Any],
    request_id: str,
    ip_address: str | None,
) -> dict[str, Any]:
    """Attach a skill, creating the catalogue entry when it is genuinely new."""
    name = str(payload.get("skill_name") or "").strip()
    if not name:
        raise ValidationError("Give the skill a name.", details={"field": "skill_name"})
    proficiency = payload.get("proficiency", 3)
    try:
        proficiency = int(proficiency)
    except (TypeError, ValueError):
        raise ValidationError("Proficiency is 1-5.", details={"field": "proficiency"}) from None
    if proficiency < 1 or proficiency > 5:
        raise ValidationError("Proficiency is 1-5.", details={"field": "proficiency"})
    skill = (
        (
            await conn.execute(
                text(
                    "INSERT INTO public.skills (name) VALUES (:name)"
                    " ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name"
                    " RETURNING id::text AS skill_id, name"
                ),
                {"name": name},
            )
        )
        .mappings()
        .first()
    )
    assert skill is not None
    await conn.execute(
        text(
            """
            INSERT INTO public.user_skills (user_id, skill_id, proficiency, years_experience,
                                            visible)
            VALUES (:uid, CAST(:sid AS uuid), :proficiency, :years, COALESCE(:visible, true))
            ON CONFLICT (user_id, skill_id) DO UPDATE
               SET proficiency = EXCLUDED.proficiency,
                   years_experience = COALESCE(
                       EXCLUDED.years_experience, public.user_skills.years_experience
                   ),
                   visible = EXCLUDED.visible
            """
        ),
        {
            "uid": user_id,
            "sid": skill["skill_id"],
            "proficiency": proficiency,
            "years": payload.get("years_experience"),
            "visible": payload.get("visible", True),
        },
    )
    await audit.record(
        conn,
        action="profile.skill_added",
        resource_type="user",
        resource_id=user_id,
        actor_user_id=user_id,
        new_values={"skill_name": skill["name"], "proficiency": proficiency},
        request_id=request_id,
        ip_address=ip_address,
    )
    return {
        "skill_id": str(skill["skill_id"]),
        "name": str(skill["name"]),
        "proficiency": proficiency,
    }


async def update_skill(
    conn: AsyncConnection,
    *,
    user_id: uuid.UUID,
    skill_id: uuid.UUID,
    changes: dict[str, Any],
    request_id: str,
    ip_address: str | None,
) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    if changes.get("proficiency") is not None:
        proficiency = int(changes["proficiency"])
        if proficiency < 1 or proficiency > 5:
            raise ValidationError("Proficiency is 1-5.", details={"field": "proficiency"})
        updates["proficiency"] = proficiency
    if changes.get("years_experience") is not None:
        updates["years_experience"] = changes["years_experience"]
    if changes.get("visible") is not None:
        updates["visible"] = bool(changes["visible"])
    if not updates:
        raise ValidationError("Nothing to update.", details={"reason": "EMPTY_UPDATE"})
    result = await conn.execute(
        text(
            "UPDATE public.user_skills SET proficiency = COALESCE(:proficiency, proficiency),"
            " years_experience = COALESCE(:years, years_experience),"
            " visible = COALESCE(:visible, visible)"
            " WHERE user_id = :uid AND skill_id = CAST(:sid AS uuid)"
            " RETURNING skill_id::text AS skill_id"
        ),
        {
            "proficiency": updates.get("proficiency"),
            "years": updates.get("years_experience"),
            "visible": updates.get("visible"),
            "uid": user_id,
            "sid": skill_id,
        },
    )
    if result.mappings().first() is None:
        raise ResourceNotFoundError("Skill not found.")
    await audit.record(
        conn,
        action="profile.skill_updated",
        resource_type="user",
        resource_id=user_id,
        actor_user_id=user_id,
        new_values=updates,
        request_id=request_id,
        ip_address=ip_address,
    )
    skills = await list_my_skills(conn, user_id=user_id)
    for entry in skills:
        if entry["skill_id"] == str(skill_id):
            return entry
    raise ResourceNotFoundError("Skill not found.")


async def remove_skill(
    conn: AsyncConnection,
    *,
    user_id: uuid.UUID,
    skill_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
) -> None:
    deleted = await conn.execute(
        text(
            "DELETE FROM public.user_skills WHERE user_id = :uid AND skill_id = CAST(:sid AS uuid)"
            " RETURNING skill_id"
        ),
        {"uid": user_id, "sid": skill_id},
    )
    if deleted.mappings().first() is None:
        raise ResourceNotFoundError("Skill not found.")
    await audit.record(
        conn,
        action="profile.skill_removed",
        resource_type="user",
        resource_id=user_id,
        actor_user_id=user_id,
        request_id=request_id,
        ip_address=ip_address,
    )


async def get_visa(conn: AsyncConnection, *, user_id: uuid.UUID) -> dict[str, Any]:
    """Owner-only work authorization. Never joined into profile responses."""
    row = (
        (
            await conn.execute(
                text(
                    "SELECT visa_status, work_authorization FROM public.user_sensitive"
                    " WHERE user_id = :uid"
                ),
                {"uid": user_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return {"visa_status": None, "work_authorization": None}
    return {"visa_status": row["visa_status"], "work_authorization": row["work_authorization"]}


async def update_visa(
    conn: AsyncConnection,
    *,
    user_id: uuid.UUID,
    payload: dict[str, Any],
    request_id: str,
    ip_address: str | None,
) -> dict[str, Any]:
    await conn.execute(
        text(
            """
            INSERT INTO public.user_sensitive (user_id, visa_status, work_authorization)
            VALUES (:uid, :status, :detail)
            ON CONFLICT (user_id) DO UPDATE
               SET visa_status = EXCLUDED.visa_status,
                   work_authorization = EXCLUDED.work_authorization
            """
        ),
        {
            "uid": user_id,
            "status": payload.get("visa_status"),
            "detail": payload.get("work_authorization"),
        },
    )
    await audit.record(
        conn,
        action="profile.visa_updated",
        resource_type="user",
        resource_id=user_id,
        actor_user_id=user_id,
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_visa(conn, user_id=user_id)
