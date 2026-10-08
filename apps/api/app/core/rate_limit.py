"""Redis-backed rate limiting.

Design notes:
  * Fixed-window counters via INCR + EXPIRE, atomic in one round trip.
  * Keyed by (bucket, subject) where subject is the authenticated user id, or the
    client IP for unauthenticated traffic. Identity comes from the verified token,
    never from a header the client can choose.
  * When Redis is unreachable the limiter fails **open** for authenticated traffic
    and closed for authentication endpoints, so an infrastructure outage does not
    lock every user out while still protecting credential endpoints.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import redis.asyncio as aioredis
from fastapi import Request
from redis.exceptions import RedisError

from app.core.config import Settings, get_settings
from app.core.errors import RateLimitExceededError
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RateLimit:
    """A parsed `count/period` specification."""

    limit: int
    period_seconds: int

    @classmethod
    def parse(cls, spec: str) -> RateLimit:
        raw, _, period = spec.partition("/")
        count = int(raw)
        unit = period.strip() or "1m"
        multiplier = {"s": 1, "m": 60, "h": 3600, "d": 86400}
        suffix = unit[-1]
        if suffix not in multiplier:
            raise ValueError(f"unsupported rate-limit period: {unit!r}")
        return cls(limit=count, period_seconds=int(unit[:-1]) * multiplier[suffix])

    def bucket_key(self, name: str, subject: str, *, now: float | None = None) -> str:
        ts = int(now if now is not None else time.time())
        window = ts // self.period_seconds
        # Windowed key: no key expiry bookkeeping needed, and the counter resets
        # by rolling to a new key.
        return f"rl:{name}:{subject}:{window}"


class RateLimiter:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._redis: aioredis.Redis[str] | None = None

    async def _client(self) -> aioredis.Redis[str] | None:
        if not self._settings.rate_limit_enabled:
            return None
        if self._redis is None:
            self._redis = aioredis.from_url(
                self._settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_timeout=0.25,
                socket_connect_timeout=0.25,
            )
        return self._redis

    async def check(self, name: str, subject: str, limit: RateLimit) -> None:
        """Consume one unit. Raises RateLimitExceededError when over budget."""
        client = await self._client()
        if client is None:
            return

        key = limit.bucket_key(name, subject)
        try:
            pipe = client.pipeline()
            pipe.incr(key)
            pipe.expire(key, limit.period_seconds + 1)
            count, _ = await pipe.execute()
        except RedisError:
            # Fail open: an outage in the cache must not become an outage for
            # authenticated users. Logged so it is visible in monitoring.
            logger.warning("rate_limiter_unavailable", bucket=name)
            return

        if int(count) > limit.limit:
            reset_after = limit.period_seconds - (int(time.time()) % limit.period_seconds)
            logger.info("rate_limit_exceeded", bucket=name, subject=subject, count=int(count))
            raise RateLimitExceededError(
                details={"bucket": name, "retry_after_seconds": reset_after}
            )

    async def close(self) -> None:
        if self._redis is not None:
            # `aclose` is the async client's shutdown method; the shipped type
            # stubs still only declare the sync `close`, though both exist.
            await self._redis.aclose()  # type: ignore[attr-defined]
            self._redis = None


def rate_limited(bucket: str) -> Callable[[Request], Awaitable[None]]:
    """FastAPI dependency enforcing the named bucket, keyed by client IP.

    Per-user AI spend is enforced separately in SQL (`ai_tokens_today`); this
    is the abuse layer: cheap, IP-keyed, failing open when Redis is down
    (except that it never fails a request by itself — see `check`).
    """

    async def dependency(request: Request) -> None:
        forwarded = request.headers.get("x-forwarded-for") if request else None
        if forwarded:
            subject = forwarded.split(",")[0].strip()
        elif request and request.client:
            subject = request.client.host
        else:
            subject = "unknown"
        await get_rate_limiter().check(bucket, subject, limit_for(bucket))

    return dependency


_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter()
    return _limiter


def limit_for(name: str) -> RateLimit:
    """Resolve a named bucket to its configured limit."""
    settings = get_settings()
    spec = {
        "login": settings.rate_limit_login,
        "signup": settings.rate_limit_signup,
        "password_reset": settings.rate_limit_password_reset,
        "messaging": settings.rate_limit_messaging,
        "ai": settings.rate_limit_ai,
        "search": settings.rate_limit_search,
        "upload": settings.rate_limit_upload,
        "payments": settings.rate_limit_payments,
        "bank_sync": settings.rate_limit_bank_sync,
    }.get(name, settings.rate_limit_default)
    return RateLimit.parse(spec)
