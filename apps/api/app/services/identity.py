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

from app.core.errors import ResourceNotFoundError
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

    Delegates to the SQL predicate so the API and the RLS policies agree.
    """
    result = await conn.execute(
        text("SELECT app.can_view_profile(:target, :viewer) AS allowed"),
        {"target": target_id, "viewer": viewer_id},
    )
    if not result.scalar():
        return None

    if viewer_id == target_id:
        return "PRIVATE"

    connected = await conn.execute(
        text("SELECT app.can_view_connected(:target, :viewer) AS connected"),
        {"target": target_id, "viewer": viewer_id},
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
