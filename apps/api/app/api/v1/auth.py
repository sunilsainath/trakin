"""Authentication endpoints.

Supabase Auth owns credentials. This router:

  * bootstraps the platform user row on first authenticated request
  * reports session and device state so the user can revoke
  * enforces the platform's own session policy (max age, revocation all)

It never accepts a password, never stores one, and never issues a token. That
keeps a single implementation of password handling (Supabase's, which is audited)
instead of two.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.api.deps import RequestContext, require_user
from app.core.errors import ResourceNotFoundError
from app.core.logging import get_logger
from app.schemas.common import AckResponse
from app.schemas.identity import SessionResponse
from app.services import audit

router = APIRouter(prefix="/auth", tags=["auth"])
logger = get_logger(__name__)

SessionContext = Annotated[tuple[RequestContext, AsyncConnection], Depends(require_user)]


@router.post("/bootstrap", response_model=AckResponse, summary="Ensure the platform user exists")
async def bootstrap(ctx_and_conn: SessionContext, response: Response) -> AckResponse:
    """Called once after signup, and defensively on every session start.

    The platform row already exists by the time this runs (provisioned on the
    first authenticated request). This call syncs email verification state
    from Supabase Auth into `public.users.email_verified_at`, which is what
    `assert_account_active` enforces — the access token itself carries no
    verifiable claim.

    Idempotent: calling it again changes nothing once synced.
    """
    ctx, conn = ctx_and_conn
    await _sync_verification(conn, ctx)
    return AckResponse(ok=True, message="Account ready.", request_id=ctx.request_id)


async def _sync_verification(conn: AsyncConnection, ctx: RequestContext) -> None:
    """Mirror Supabase's confirmation timestamp into the platform row."""
    import httpx

    from app.core.config import get_settings

    settings = get_settings()
    base = (settings.supabase_url or "").rstrip("/")
    service_key = settings.supabase_service_role_key.get_secret_value()
    if not base or not service_key:
        logger.warning("verification_sync_skipped", reason="supabase_not_configured")
        return

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{base}/auth/v1/admin/users/{ctx.auth.auth_id}",
                headers={
                    "apikey": service_key,
                    "Authorization": f"Bearer {service_key}",
                },
            )
    except Exception as exc:  # noqa: BLE001 - sync is best-effort, never fatal
        logger.warning("verification_sync_failed", error=str(exc)[:200])
        return

    if response.status_code != 200:
        logger.warning("verification_sync_failed", status=response.status_code)
        return

    payload = response.json()
    confirmed_at = payload.get("email_confirmed_at") or payload.get("confirmed_at")
    if confirmed_at:
        # Confirmation activates the account: ck_user_email_verified_status
        # forbids ACTIVE without a timestamp, and assert_account_active
        # rejects PENDING_VERIFICATION.
        await conn.execute(
            text(
                """
                UPDATE public.users
                   SET email = COALESCE(NULLIF(:email, ''), email),
                       email_verified_at = COALESCE(
                           CAST(:confirmed AS timestamptz), email_verified_at),
                       status = 'ACTIVE'
                 WHERE id = :uid
                """
            ),
            {"email": ctx.auth.email, "confirmed": confirmed_at, "uid": ctx.user_id},
        )


@router.get("/me", summary="Current identity")
async def me(ctx_and_conn: SessionContext) -> dict[str, Any]:
    ctx, _conn = ctx_and_conn
    return {
        "user_id": str(ctx.user_id),
        "auth_id": ctx.auth.auth_id,
        "email": ctx.auth.email,
        "email_verified": ctx.auth.email_verified,
        "provider": ctx.auth.provider,
        "session_id": ctx.auth.session_id,
    }


@router.get("/sessions", response_model=list[SessionResponse], summary="List active sessions")
async def list_sessions(
    ctx_and_conn: SessionContext,
    response: Response,
) -> list[SessionResponse]:
    """Devices with a live session, so the user can spot one they do not recognise."""
    ctx, conn = ctx_and_conn

    rows = (
        (
            await conn.execute(
                text(
                    """
                SELECT s.public_id, s.device_label, host(s.ip_address) AS ip_address,
                       s.created_at, s.last_seen_at, s.supabase_session_id
                  FROM platform.user_sessions s
                 WHERE s.user_id = :uid AND s.revoked_at IS NULL
                 ORDER BY s.last_seen_at DESC
                 LIMIT 50
                """
                ),
                {"uid": ctx.user_id},
            )
        )
        .mappings()
        .all()
    )

    return [
        SessionResponse(
            public_id=str(r["public_id"]),
            device=r["device_label"],
            ip_address=r["ip_address"],
            current=r["supabase_session_id"] == ctx.auth.session_id,
            created_at=r["created_at"],
            last_seen_at=r["last_seen_at"],
        )
        for r in rows
    ]


@router.post(
    "/sessions/revoke-all",
    response_model=AckResponse,
    summary="Revoke every session except the current one",
)
async def revoke_all_sessions(ctx_and_conn: SessionContext, response: Response) -> AckResponse:
    """Security action: signs out every other device.

    The current session is preserved so the user is not logged out of the device
    they initiated this from.
    """
    ctx, conn = ctx_and_conn

    result = await conn.execute(
        text(
            """
            UPDATE platform.user_sessions
               SET revoked_at = now(), revoked_reason = 'user_revoke_all'
             WHERE user_id = :uid
               AND revoked_at IS NULL
               AND (supabase_session_id IS DISTINCT FROM :sid)
            """
        ),
        {"uid": ctx.user_id, "sid": ctx.auth.session_id},
    )
    revoked = result.rowcount or 0

    await audit.record(
        conn,
        action="auth.sessions_revoked",
        resource_type="user",
        resource_id=ctx.user_id,
        actor_user_id=ctx.user_id,
        new_values={"revoked_sessions": revoked},
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
    )

    logger.info("sessions_revoked", user_id=str(ctx.user_id), revoked=revoked)
    return AckResponse(
        ok=True,
        message=f"{revoked} session(s) signed out.",
        request_id=ctx.request_id,
    )


@router.post(
    "/sessions/{session_public_id}/revoke",
    response_model=AckResponse,
    summary="Revoke one session",
)
async def revoke_session(ctx_and_conn: SessionContext, session_public_id: str) -> AckResponse:
    ctx, conn = ctx_and_conn

    result = await conn.execute(
        text(
            """
            UPDATE platform.user_sessions
               SET revoked_at = now(), revoked_reason = 'user_revoke_single'
             WHERE user_id = :uid
               AND public_id = :pid
               AND revoked_at IS NULL
            """
        ),
        {"uid": ctx.user_id, "pid": session_public_id},
    )

    if (result.rowcount or 0) == 0:
        raise ResourceNotFoundError("Session not found.")

    await audit.record(
        conn,
        action="auth.session_revoked",
        resource_type="user_session",
        resource_public_id=session_public_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
    )
    return AckResponse(ok=True, message="Session signed out.", request_id=ctx.request_id)
