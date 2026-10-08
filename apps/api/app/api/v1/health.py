"""Health and readiness.

`/health` is a liveness probe: it answers as long as the process is up, so a
database blip does not cause the orchestrator to kill healthy containers.

`/ready` is a readiness probe: it checks the dependencies a request actually
needs, and returns 503 when the service cannot serve traffic. Azure Front Door
routes on this.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import session_scope

router = APIRouter(tags=["health"])
logger = get_logger(__name__)

STARTED_AT = time.time()


@router.get("/health", summary="Liveness probe")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": get_settings().platform_name,
        "uptime_seconds": round(time.time() - STARTED_AT, 1),
    }


@router.get("/ready", summary="Readiness probe")
async def ready(response: Response) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    healthy = True

    # SELECT 1 does not touch a table, so it proves nothing about the pool or the
    # database role. Reading platform.feature_flags exercises the connection, the
    # pool checkout hook and a granted table in one round trip.
    try:
        async with session_scope() as conn:
            await conn.execute(text("SELECT count(*) FROM platform.feature_flags"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        logger.warning("readiness_database_check_failed", error=str(exc)[:300])
        checks["database"] = f"error: {type(exc).__name__}"
        healthy = False

    try:
        from app.core.rate_limit import get_rate_limiter

        client = await get_rate_limiter()._client()
        if client is not None:
            await client.ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        # Redis is a cache, not a source of truth. Its absence degrades rate
        # limiting, so it must not take the service out of rotation.
        checks["redis"] = f"degraded: {type(exc).__name__}"

    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {"status": "ok" if healthy else "unavailable", "checks": checks}


@router.get("/version", summary="Build and version information")
async def version() -> dict[str, Any]:
    settings = get_settings()
    return {
        "name": settings.platform_name,
        "environment": settings.environment,
        "api_version": "v1",
        "ai_gateway_enabled": settings.ai_gateway_enabled,
    }
