"""Alembic environment.

The authoritative schema is the ordered SQL in `supabase/migrations`, applied by
`scripts/apply_migrations.py`. This environment exists so `alembic check` can
prove the running database has not drifted from that source, and so future
changes made through SQLAlchemy models stay in step.

`compare_type=True` is on deliberately: a drifted column type is exactly the kind
of difference that produces subtle query bugs rather than an obvious failure.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.core.config import get_settings

# Importing this module installs the selector event loop on Windows, which psycopg
# requires. Without it Alembic fails on the first connection with an opaque
# InterfaceError instead of a clear message.
from app.core.event_loop import ensure_compatible_event_loop

ensure_compatible_event_loop()

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def admin_url() -> str:
    """The DSN to use for migrations.

    `DATABASE_ADMIN_URL` is preferred because Alembic needs DDL rights, which the
    application role deliberately lacks. It falls back to the application URL so
    a developer with a single configured database is not blocked.
    """
    import os

    admin = os.environ.get("DATABASE_ADMIN_URL")
    if admin:
        return admin

    settings = get_settings()
    return settings.async_database_url


def normalise(url: str) -> str:
    """Alembic's async engine needs the driver named in the URL."""
    if url.startswith("postgresql+psycopg_async://"):
        return url.replace("postgresql+psycopg_async://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    return url


# The value is escaped because a DSN may contain '%' in its password.
config.set_main_option("sqlalchemy.url", normalise(admin_url()).replace("%", "%%"))


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
