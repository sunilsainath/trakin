"""Identity endpoints: profile, privacy, connections summary."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.api.deps import RequestContext, require_user
from app.schemas.common import AckResponse, Page, build_page, clamp_limit
from app.schemas.identity import (
    EducationRequest,
    ExperienceRequest,
    NotificationPreferenceItem,
    SearchHit,
    SkillRequest,
    UpdateMeRequest,
    UserProfileResponse,
    VisaRequest,
)
from app.services import audit, identity

router = APIRouter(prefix="/users", tags=["identity"])

UserContext = Annotated[tuple[RequestContext, AsyncConnection], Depends(require_user)]


@router.get("/me", response_model=dict[str, Any], summary="Current user profile and settings")
async def get_me(ctx_and_conn: UserContext) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await identity.get_me(conn, ctx.user_id)


@router.patch("/me", response_model=dict[str, Any], summary="Update own profile")
async def update_me(ctx_and_conn: UserContext, payload: UpdateMeRequest) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await identity.update_me(
        conn,
        user_id=ctx.user_id,
        changes=payload.model_dump(exclude_unset=True),
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
    )


@router.put(
    "/me/privacy",
    response_model=AckResponse,
    summary="Set field-level visibility",
)
async def set_privacy(
    ctx_and_conn: UserContext,
    payload: dict[str, str],
) -> AckResponse:
    """Visibility per field: PUBLIC, CONNECTIONS or PRIVATE.

    Applied server-side before any profile payload is assembled, so a field can
    never be returned by accident.
    """
    ctx, conn = ctx_and_conn
    await identity.set_privacy(
        conn,
        user_id=ctx.user_id,
        field_visibility=payload,
        request_id=ctx.request_id,
    )
    return AckResponse(ok=True, message="Privacy settings saved.", request_id=ctx.request_id)


@router.post("/me/onboarding", summary="Complete onboarding")
async def complete_onboarding(ctx_and_conn: UserContext) -> dict[str, Any]:
    """Mark onboarding complete once the profile has a name.

    The workspace gate sends users here until this is set; the frontend calls
    it from the final onboarding step. Requires a real name so company member
    lists never show blanks.
    """
    ctx, conn = ctx_and_conn
    row = (
        (
            await conn.execute(
                text(
                    "SELECT first_name, last_name, onboarding_completed_at"
                    " FROM public.users WHERE id = :uid"
                ),
                {"uid": ctx.user_id},
            )
        )
        .mappings()
        .first()
    )
    if not row or not (row["first_name"] or "").strip() or not (row["last_name"] or "").strip():
        from app.core.errors import ValidationError

        raise ValidationError(
            "Add your first and last name before finishing onboarding.",
            details={"reason": "PROFILE_INCOMPLETE"},
        )
    await conn.execute(
        text(
            "UPDATE public.users SET onboarding_completed_at = COALESCE("
            "onboarding_completed_at, now()) WHERE id = :uid"
        ),
        {"uid": ctx.user_id},
    )
    await audit.record(
        conn,
        action="profile.onboarding_completed",
        resource_type="user",
        resource_id=ctx.user_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
    )
    return await identity.get_me(conn, ctx.user_id)


@router.get(
    "/me/notification-preferences",
    summary="My per-category notification channels",
)
async def get_my_notification_preferences(ctx_and_conn: UserContext) -> list[dict[str, Any]]:
    ctx, conn = ctx_and_conn
    return await identity.get_notification_preferences(conn, user_id=ctx.user_id)


@router.put(
    "/me/notification-preferences",
    summary="Set per-category notification channels",
)
async def set_my_notification_preferences(
    ctx_and_conn: UserContext, payload: list[NotificationPreferenceItem]
) -> list[dict[str, Any]]:
    """Unknown categories are rejected; delivery is in-app/email/push per row."""
    ctx, conn = ctx_and_conn
    return await identity.set_notification_preferences(
        conn,
        user_id=ctx.user_id,
        preferences=[item.model_dump() for item in payload],
        request_id=ctx.request_id,
    )


# --------------------------------------------------------------- career history
# All own-profile only: the rows are the caller's, addressed by internal id, and
# every statement is scoped to the actor. Visa status is protected data and is
# only ever returned to its owner.
@router.get("/me/education", summary="My education history")
async def list_my_education(ctx_and_conn: UserContext) -> list[dict[str, Any]]:
    ctx, conn = ctx_and_conn
    return await identity.list_education(conn, user_id=ctx.user_id)


@router.post("/me/education", status_code=201, summary="Add an education entry")
async def add_my_education(ctx_and_conn: UserContext, payload: EducationRequest) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await identity.add_education(
        conn,
        user_id=ctx.user_id,
        payload=payload.model_dump(),
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
    )


@router.patch("/me/education/{entry_id}", summary="Update an education entry")
async def update_my_education(
    ctx_and_conn: UserContext, entry_id: uuid.UUID, payload: EducationRequest
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await identity.update_education(
        conn,
        user_id=ctx.user_id,
        education_id=entry_id,
        changes=payload.model_dump(exclude_unset=True),
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
    )


@router.delete("/me/education/{entry_id}", response_model=AckResponse, summary="Remove an entry")
async def delete_my_education(ctx_and_conn: UserContext, entry_id: uuid.UUID) -> AckResponse:
    ctx, conn = ctx_and_conn
    await identity.delete_education(
        conn,
        user_id=ctx.user_id,
        education_id=entry_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
    )
    return AckResponse(ok=True, message="Education entry removed.", request_id=ctx.request_id)


@router.get("/me/experience", summary="My career history")
async def list_my_experience(ctx_and_conn: UserContext) -> list[dict[str, Any]]:
    ctx, conn = ctx_and_conn
    return await identity.list_experience(conn, user_id=ctx.user_id)


@router.post("/me/experience", status_code=201, summary="Add a career entry")
async def add_my_experience(
    ctx_and_conn: UserContext, payload: ExperienceRequest
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await identity.add_experience(
        conn,
        user_id=ctx.user_id,
        payload=payload.model_dump(),
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
    )


@router.patch("/me/experience/{entry_id}", summary="Update a career entry")
async def update_my_experience(
    ctx_and_conn: UserContext, entry_id: uuid.UUID, payload: ExperienceRequest
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await identity.update_experience(
        conn,
        user_id=ctx.user_id,
        experience_id=entry_id,
        changes=payload.model_dump(exclude_unset=True),
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
    )


@router.delete("/me/experience/{entry_id}", response_model=AckResponse, summary="Remove an entry")
async def delete_my_experience(ctx_and_conn: UserContext, entry_id: uuid.UUID) -> AckResponse:
    ctx, conn = ctx_and_conn
    await identity.delete_experience(
        conn,
        user_id=ctx.user_id,
        experience_id=entry_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
    )
    return AckResponse(ok=True, message="Career entry removed.", request_id=ctx.request_id)


@router.get("/me/skills", summary="My skills")
async def list_my_skills(ctx_and_conn: UserContext) -> list[dict[str, Any]]:
    ctx, conn = ctx_and_conn
    return await identity.list_my_skills(conn, user_id=ctx.user_id)


@router.post("/me/skills", status_code=201, summary="Add a skill")
async def add_my_skill(ctx_and_conn: UserContext, payload: SkillRequest) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await identity.add_skill(
        conn,
        user_id=ctx.user_id,
        payload=payload.model_dump(),
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
    )


@router.patch("/me/skills/{skill_id}", summary="Update a skill")
async def update_my_skill(
    ctx_and_conn: UserContext, skill_id: uuid.UUID, payload: SkillRequest
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await identity.update_skill(
        conn,
        user_id=ctx.user_id,
        skill_id=skill_id,
        changes=payload.model_dump(exclude_unset=True),
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
    )


@router.delete("/me/skills/{skill_id}", response_model=AckResponse, summary="Remove a skill")
async def remove_my_skill(ctx_and_conn: UserContext, skill_id: uuid.UUID) -> AckResponse:
    ctx, conn = ctx_and_conn
    await identity.remove_skill(
        conn,
        user_id=ctx.user_id,
        skill_id=skill_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
    )
    return AckResponse(ok=True, message="Skill removed.", request_id=ctx.request_id)


@router.get("/me/visa", summary="My work authorization (owner-only)")
async def get_my_visa(ctx_and_conn: UserContext) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await identity.get_visa(conn, user_id=ctx.user_id)


@router.put("/me/visa", summary="Set my work authorization (owner-only)")
async def set_my_visa(ctx_and_conn: UserContext, payload: VisaRequest) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await identity.update_visa(
        conn,
        user_id=ctx.user_id,
        payload=payload.model_dump(),
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
    )


@router.get(
    "/{public_id}",
    response_model=UserProfileResponse,
    summary="View a profile as the caller is permitted to see it",
)
async def get_profile(ctx_and_conn: UserContext, public_id: str) -> dict[str, Any]:
    """Returns 404, not 403, for a profile the caller may not see.

    Distinguishing the two would let a caller enumerate which profiles exist.
    """
    ctx, conn = ctx_and_conn

    row = (
        (
            await conn.execute(
                text("SELECT u.id::text AS id FROM public.users u WHERE u.public_id = :pid"),
                {"pid": public_id},
            )
        )
        .mappings()
        .first()
    )

    if row is None:
        from app.core.errors import ResourceNotFoundError

        raise ResourceNotFoundError("Profile not found.")

    return await identity.get_profile(
        conn, viewer_id=ctx.user_id, target_user_id=uuid.UUID(str(row["id"]))
    )


@router.get(
    "/{public_id}/connections",
    response_model=Page[SearchHit],
    summary="List a user's visible connections",
)
async def list_connections(
    ctx_and_conn: UserContext,
    public_id: str,
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None),
) -> Page[Any]:
    ctx, conn = ctx_and_conn

    from app.core.errors import ResourceNotFoundError
    from app.schemas.common import decode_cursor

    row = (
        (
            await conn.execute(
                text("SELECT u.id::text AS id FROM public.users u WHERE u.public_id = :pid"),
                {"pid": public_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ResourceNotFoundError("User not found.")

    params: dict[str, Any] = {"target": row["id"], "limit": clamp_limit(limit) + 1}
    after_name: str | None = None
    if cursor:
        try:
            after_name = decode_cursor(cursor).get("name")
        except ValueError as exc:
            raise ResourceNotFoundError("Invalid cursor.") from exc
        if after_name:
            params["after_name"] = after_name

    rows = (
        (
            await conn.execute(
                text(
                    """
                SELECT u.public_id,
                       u.first_name || ' ' || u.last_name AS display_name,
                       p.headline
                  FROM public.connections c
                  JOIN public.users u ON u.id = CASE
                        WHEN c.requester_id = CAST(:target AS uuid) THEN c.addressee_id
                        ELSE c.requester_id END
                  LEFT JOIN public.user_profiles p ON p.user_id = u.id
                 WHERE c.status = 'ACCEPTED'
                   AND (c.requester_id = CAST(:target AS uuid)
                     OR c.addressee_id = CAST(:target AS uuid))
                   AND NOT app.is_blocked(u.id, NULL, :viewer)
                   AND (:after_name IS NULL
                        || u.first_name || ' ' || u.last_name > :after_name)
                 ORDER BY u.first_name || ' ' || u.last_name
                 LIMIT :limit
                """
                ),
                {**params, "viewer": ctx.user_id},
            )
        )
        .mappings()
        .all()
    )

    return build_page(
        [dict(r) for r in rows],
        limit=clamp_limit(limit),
        cursor_keys=("display_name",),
        request_id=ctx.request_id,
    )
