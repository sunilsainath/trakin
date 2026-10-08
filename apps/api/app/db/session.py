"""Database engine, session identity and the transaction boundary.

The central idea: **every** query runs inside a transaction that has been told who
the caller is. `SET LOCAL app.user_id` / `app.company_id` are read by every RLS
policy, so a bug in application code degrades to "no rows", never "all rows".

The connection uses a non-superuser role. `mytrakin_api` is neither superuser nor
BYPASSRLS, so PostgreSQL enforces the policies.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

# Must precede the engine: psycopg's async driver cannot run on the Windows
# proactor loop. Importing this module is enough to install a compatible policy,
# so tests, workers and scripts behave like the API process.
from app.core.event_loop import ensure_compatible_event_loop

ensure_compatible_event_loop()

from app.core.config import Settings, get_settings  # noqa: E402
from app.core.logging import get_logger  # noqa: E402

logger = get_logger(__name__)

_engine: AsyncEngine | None = None
_connection_factory: Callable[[], AsyncConnection] | None = None


def build_engine(settings: Settings | None = None) -> AsyncEngine:
    settings = settings or get_settings()
    url = settings.async_database_url

    kwargs: dict[str, Any] = {
        "echo": False,
        "pool_pre_ping": True,
        "future": True,
        # Named server-side prepared statements do not survive a transaction
        # pooler (Supabase PgBouncer multiplexes backends, so a statement name
        # prepared on one backend collides on the next: DuplicatePreparedStatement).
        # Every other connection in this repo (tests, scripts, workers) already
        # connects with prepare_threshold=None; the app engine must match.
        "connect_args": {"prepare_threshold": None},
    }

    if settings.db_pooled:
        kwargs.update(
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout_s,
        )
    else:
        # A transaction pooler such as PgBouncer in transaction mode already
        # multiplexes; a client-side pool on top only exhausts server slots.
        kwargs["poolclass"] = NullPool

    engine = create_async_engine(url, **kwargs)

    # A runaway query must not hold a pooled connection indefinitely, so timeouts
    # are applied once per physical connection as it is checked out.
    engine.pool._on_attach = _on_attach_set_timeouts  # type: ignore[attr-defined]

    return engine


async def _on_attach_set_timeouts(dbapi_conn: Any, _record: Any) -> None:
    """Pool checkout hook: apply per-connection timeouts.

    Runs on a raw DBAPI connection during sync engine setup, so the statements
    are plain strings with no bound parameters.
    """
    raw = getattr(dbapi_conn, "driver_connection", None) or dbapi_conn
    with raw.cursor() as cur:
        cur.execute("SET statement_timeout = '15000ms'")
        cur.execute("SET idle_in_transaction_session_timeout = '30s'")


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = build_engine()
    return _engine


def get_connection_factory() -> Callable[[], AsyncConnection]:
    """Hand out raw connections, not ORM sessions.

    The data layer issues SQL directly and every caller annotates its argument as
    `AsyncConnection`. An `AsyncSession` would add an identity map, autoflush and
    a unit-of-work lifecycle that nothing here wants, and would silently widen the
    types across the whole codebase.
    """
    global _connection_factory
    if _connection_factory is None:
        _connection_factory = get_engine().connect
    return _connection_factory


async def dispose_engine() -> None:
    global _engine, _connection_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _connection_factory = None


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncConnection]:
    """A plain transaction with no identity. For workers and system jobs."""
    factory = get_connection_factory()
    async with factory() as conn, conn.begin():
        yield conn


@asynccontextmanager
async def request_session(
    *,
    user_id: uuid.UUID | None,
    company_id: uuid.UUID | None,
    request_id: str,
    actor_type: str = "USER",
) -> AsyncIterator[AsyncConnection]:
    """A transaction carrying the caller's identity for RLS.

    This is the only way a request handler should obtain a connection. The SET
    LOCAL values are transaction-scoped, so they cannot leak to the next request
    even when the connection returns to the pool.
    """
    factory = get_connection_factory()
    async with factory() as conn, conn.begin():
        await conn.execute(
            text("SELECT set_config('app.user_id', :v, true)"), {"v": str(user_id or "")}
        )
        await conn.execute(
            text("SELECT set_config('app.company_id', :v, true)"), {"v": str(company_id or "")}
        )
        await conn.execute(text("SELECT set_config('app.request_id', :v, true)"), {"v": request_id})
        await conn.execute(text("SELECT set_config('app.actor_type', :v, true)"), {"v": actor_type})
        yield conn


async def set_identity(
    conn: AsyncConnection,
    *,
    user_id: uuid.UUID | None,
    company_id: uuid.UUID | None = None,
    request_id: str = "",
    actor_type: str = "USER",
) -> None:
    """Change identity mid-transaction (e.g. switching company context)."""
    await conn.execute(
        text("SELECT set_config('app.user_id', :v, true)"), {"v": str(user_id or "")}
    )
    await conn.execute(
        text("SELECT set_config('app.company_id', :v, true)"), {"v": str(company_id or "")}
    )
    if request_id:
        await conn.execute(text("SELECT set_config('app.request_id', :v, true)"), {"v": request_id})
    await conn.execute(text("SELECT set_config('app.actor_type', :v, true)"), {"v": actor_type})


# ------------------------------------------------------------------ utilities
async def has_permission(
    conn: AsyncConnection,
    company_id: uuid.UUID,
    permission: str,
    user_id: uuid.UUID | None = None,
) -> bool:
    """Delegate to the single SQL decision point.

    Using the same function the RLS policies use means application and database
    can never disagree about what a role grants.
    """
    sql = text("SELECT app.has_permission(:company_id, :permission, :user_id)")
    result = await conn.execute(
        sql,
        {"company_id": company_id, "permission": permission, "user_id": user_id},
    )
    return bool(result.scalar())


async def effective_permissions(
    conn: AsyncConnection,
    company_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
) -> list[str]:
    result = await conn.execute(
        text("SELECT app.my_permissions(:company_id, :user_id)"),
        {"company_id": company_id, "user_id": user_id},
    )
    keys = result.scalar()
    return sorted(keys or [])
