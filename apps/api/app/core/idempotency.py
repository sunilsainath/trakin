"""Idempotency for financial and externally-visible operations.

The pattern, in one place, so no endpoint has to reinvent it:

  1. hash the canonical request body
  2. try to claim the key
     * a fresh key is claimed and the request proceeds
     * a matching key with a stored response replays that response
     * a key reused with a *different* body is rejected as a client bug
  3. on success, store the response with its status code

A crash between claiming and storing leaves an in-flight row. A later request with
the same key sees `locked_at` set and is refused rather than executing twice.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.errors import IdempotencyKeyError, IdempotencyKeyReuseError
from app.core.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")

# Older than this, an in-flight claim is considered abandoned and may be retried.
IN_FLIGHT_TTL_SECONDS = 15 * 60


@dataclass(frozen=True, slots=True)
class IdempotentResult:
    body: Any
    status_code: int
    replayed: bool


def canonical_hash(payload: Any) -> str:
    """Stable hash of a request body, insensitive to key order."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def run_idempotent(
    conn: AsyncConnection,
    *,
    key: str,
    scope: str,
    user_id: str | None,
    company_id: str | None,
    payload: Any,
    handler: Callable[[], Awaitable[tuple[Any, int]]],
) -> IdempotentResult:
    """Execute `handler` at most once per (key, scope)."""
    request_hash = canonical_hash(payload)

    existing = (
        (
            await conn.execute(
                text(
                    """
                SELECT request_hash, response_status, response_body, locked_at
                  FROM platform.idempotency_keys
                 WHERE key = :key
                """
                ),
                {"key": key},
            )
        )
        .mappings()
        .first()
    )

    if existing is not None:
        if existing["request_hash"] != request_hash:
            raise IdempotencyKeyReuseError(
                "This Idempotency-Key was already used with a different request body."
            )

        if existing["locked_at"] is not None:
            import datetime as _dt

            locked_at = existing["locked_at"]
            now = _dt.datetime.now(_dt.UTC)
            if locked_at.tzinfo is None:
                locked_at = locked_at.replace(tzinfo=_dt.UTC)
            if (now - locked_at).total_seconds() < IN_FLIGHT_TTL_SECONDS:
                raise IdempotencyKeyReuseError(
                    "A request with this Idempotency-Key is still in progress."
                )
            # Abandoned claim: take it over.
            await conn.execute(
                text("UPDATE platform.idempotency_keys SET locked_at = now() WHERE key = :key"),
                {"key": key},
            )
        else:
            logger.info("idempotent_replay", scope=scope)
            return IdempotentResult(
                body=existing["response_body"],
                status_code=int(existing["response_status"] or 200),
                replayed=True,
            )
    else:
        claimed = (
            (
                await conn.execute(
                    text(
                        """
                    INSERT INTO platform.idempotency_keys
                      (key, scope, company_id, user_id, request_hash, locked_at)
                    VALUES (:key, :scope, :company_id, :user_id, :hash, now())
                    ON CONFLICT (key) DO NOTHING
                    RETURNING key
                    """
                    ),
                    {
                        "key": key,
                        "scope": scope,
                        "company_id": company_id,
                        "user_id": user_id,
                        "hash": request_hash,
                    },
                )
            )
            .mappings()
            .first()
        )

        if claimed is None:
            # Lost the race; the winner's response is authoritative.
            raise IdempotencyKeyReuseError(
                "A concurrent request is already using this Idempotency-Key."
            )

    body, status_code = await handler()

    await conn.execute(
        text(
            """
            UPDATE platform.idempotency_keys
               SET response_status = :status, response_body = :body, locked_at = NULL
             WHERE key = :key
            """
        ),
        {"key": key, "status": status_code, "body": json.dumps(body, default=str)},
    )

    return IdempotentResult(body=body, status_code=status_code, replayed=False)


def require_idempotency_key(headers: dict[str, str]) -> str:
    """Read and validate the Idempotency-Key header."""
    key = headers.get("idempotency-key") or headers.get("Idempotency-Key")
    if not key:
        raise IdempotencyKeyError()
    key = key.strip()
    if not 8 <= len(key) <= 200:
        raise IdempotencyKeyError("Idempotency-Key must be between 8 and 200 characters.")
    if not all(c.isalnum() or c in "-_:." for c in key):
        raise IdempotencyKeyError(
            "Idempotency-Key may only contain letters, digits, '-', '_', ':' and '.'."
        )
    return key
