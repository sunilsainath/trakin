"""Platform administration (not company administration).

Every route requires an explicit platform grant (`require_platform_admin`),
which company roles — including company SUPER_ADMINs — can never satisfy.
The surface is read-only overview data: user/company management actions stay
in the database/API support tooling, never in a company-reachable UI.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.api.deps import RequestContext, require_platform_admin

router = APIRouter(prefix="/admin", tags=["admin"])

PlatformAdmin = Annotated[tuple[RequestContext, AsyncConnection], Depends(require_platform_admin)]


@router.get("/overview", summary="Platform overview metrics")
async def overview(ctx_and_conn: PlatformAdmin) -> dict[str, Any]:
    """Counts only — no row contents. Support triage, not browsing."""
    ctx, conn = ctx_and_conn
    row = (
        (
            await conn.execute(
                text(
                    """
                    SELECT (SELECT count(*) FROM public.users) AS users,
                           (SELECT count(*) FROM public.users
                             WHERE status = 'PENDING_VERIFICATION') AS pending_verification,
                           (SELECT count(*) FROM public.companies
                             WHERE deleted_at IS NULL) AS companies,
                           (SELECT count(*) FROM platform.audit_logs
                             WHERE occurred_at > now() - interval '24 hours') AS audit_24h,
                           (SELECT COALESCE(sum(prompt_tokens + completion_tokens), 0)
                              FROM public.ai_usage_events
                             WHERE created_at > now() - interval '24 hours') AS ai_tokens_24h,
                           (SELECT count(*) FROM platform.outbox_events
                             WHERE published_at IS NULL) AS outbox_pending
                    """
                )
            )
        )
        .mappings()
        .first()
    )
    return {**dict(row or {}), "request_id": ctx.request_id}


@router.get("/flags", summary="Platform feature flags")
async def list_flags(ctx_and_conn: PlatformAdmin) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    rows = (
        (
            await conn.execute(
                text(
                    "SELECT key, description, enabled, rollout_pct, updated_at"
                    " FROM platform.feature_flags ORDER BY key"
                )
            )
        )
        .mappings()
        .all()
    )
    return {"data": [dict(r) for r in rows], "request_id": ctx.request_id}
