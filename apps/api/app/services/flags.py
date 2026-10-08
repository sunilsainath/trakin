"""Feature flags.

Resolution order, most specific first:

    user override  >  company override  >  platform default (with rollout %)

An AI or automation capability is only enabled when the platform flag, the
company override and the user's own consent all say yes. A flag alone is not
sufficient: `GET /ai/capabilities` reports the resolved value, and the AI gateway
checks it before every call.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ResolvedFlag:
    key: str
    enabled: bool
    config: dict[str, Any]


async def is_enabled(
    conn: AsyncConnection,
    key: str,
    *,
    company_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> bool:
    return (await resolve(conn, key, company_id=company_id, user_id=user_id)).enabled


async def resolve(
    conn: AsyncConnection,
    key: str,
    *,
    company_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> ResolvedFlag:
    """Resolve a flag through the override chain."""
    override = (
        (
            await conn.execute(
                text(
                    """
                SELECT enabled, config
                  FROM platform.feature_flag_overrides
                 WHERE flag_key = :key
                   AND (
                        (scope_type = 'USER'     AND scope_id = :user_id)
                     OR (scope_type = 'COMPANY' AND scope_id = :company_id)
                   )
                 ORDER BY scope_type   -- 'COMPANY' < 'USER', so the user wins
                 LIMIT 1
                """
                ),
                {"key": key, "user_id": user_id, "company_id": company_id},
            )
        )
        .mappings()
        .first()
    )

    if override is not None:
        return ResolvedFlag(
            key=key, enabled=bool(override["enabled"]), config=dict(override["config"] or {})
        )

    base = (
        (
            await conn.execute(
                text(
                    """
                    SELECT enabled, rollout_pct, config
                      FROM platform.feature_flags
                     WHERE key = :key
                    """
                ),
                {"key": key},
            )
        )
        .mappings()
        .first()
    )

    if base is None:
        # An unknown flag is off. Defaulting to on would mean a typo silently
        # enables a capability.
        logger.warning("unknown_feature_flag", flag=key)
        return ResolvedFlag(key=key, enabled=False, config={})

    enabled = bool(base["enabled"])
    pct = int(base["rollout_pct"])

    if enabled and pct < 100:
        subject = str(company_id or user_id or "")
        if not subject:
            enabled = False
        else:
            # Stable bucketing: the same subject always lands in the same slice.
            digest = hashlib.sha256(f"{key}:{subject}".encode()).hexdigest()
            enabled = int(digest[:8], 16) % 100 < pct

    return ResolvedFlag(key=key, enabled=enabled, config=dict(base["config"] or {}))


async def resolve_many(
    conn: AsyncConnection,
    keys: list[str],
    *,
    company_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> dict[str, ResolvedFlag]:
    out: dict[str, ResolvedFlag] = {}
    for key in keys:
        out[key] = await resolve(conn, key, company_id=company_id, user_id=user_id)
    return out


async def require(
    conn: AsyncConnection,
    key: str,
    *,
    company_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> ResolvedFlag:
    """Resolve a flag, raising a typed 503 when it is off.

    Used by AI and payment endpoints so a disabled capability returns
    FEATURE_DISABLED rather than a fabricated success.
    """
    from app.core.errors import FeatureDisabledError

    flag = await resolve(conn, key, company_id=company_id, user_id=user_id)
    if not flag.enabled:
        raise FeatureDisabledError(
            f"The {key} capability is not enabled for this account.",
            details={"flag": key},
        )
    return flag
