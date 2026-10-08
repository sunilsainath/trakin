"""Shared data-access helpers for the domain services.

Every tenant-scoped lookup goes through `resolve_scoped`, which adds the company
predicate to the WHERE clause as well as relying on RLS. The redundancy is
deliberate: RLS is the backstop, the explicit predicate is what keeps a bug in a
policy from becoming a cross-tenant read.

`table` is never caller-supplied from a request body — the arguments below are
module-level string literals in the service layer.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.errors import ResourceNotFoundError
from app.core.logging import get_logger

logger = get_logger(__name__)

# Column every tenant-scoped entity exposes for display. Not a security boundary:
# it exists so one SELECT shape works across projects, roles, contracts, ...
_UUID_TABLES: frozenset[str] = frozenset(
    {
        "companies",
        "project_roles",
        "projects",
        "sows",
        "contracts",
        "contract_roles",
        "contract_line_items",
        "contract_parties",
        "contract_approval_steps",
        "sow_roles",
        "assignments",
        "timesheets",
        "timesheet_entries",
        "timesheet_approvals",
        "timesheet_revisions",
        "leave_policies",
        "leave_requests",
        "invoices",
        "invoice_items",
        "invoice_approvals",
        "billing_runs",
        "payments",
        "payment_allocations",
        "payment_matches",
        "payment_accounts",
        "payment_requests",
        "bank_connections",
        "bank_accounts",
        "documents",
        "document_versions",
        "msas",
        "ai_insights",
        "ai_extractions",
        "ai_actions",
        "ai_automations",
    }
)

# Tables addressed by their internal uuid only (no public identifier is exposed
# to the API, so guessing one yields nothing).
_UUID_ONLY_TABLES: frozenset[str] = frozenset(
    {
        "bank_transactions",
        "company_roles",
        "company_memberships",
        "users",
    }
)

ALLOWED_TABLES: frozenset[str] = _UUID_TABLES | _UUID_ONLY_TABLES

# Tables without a `deleted_at` column. Filtering on it would be a SQL error, so
# it is applied only where the column exists.
_NO_SOFT_DELETE: frozenset[str] = _UUID_ONLY_TABLES


def _assert_table(table: str) -> None:
    if table not in ALLOWED_TABLES:
        raise ResourceNotFoundError("Unknown resource type.")


async def resolve_scoped(
    conn: AsyncConnection,
    table: str,
    public_id: str,
    company_id: uuid.UUID,
    *,
    columns: str = "*",
    lock: bool = False,
    include_deleted: bool = False,
) -> dict[str, Any]:
    """Fetch one tenant-scoped row by its public id, or raise 404.

    A row belonging to another company is indistinguishable from a row that does
    not exist: both raise `RESOURCE_NOT_FOUND`, so probing never confirms that an
    identifier is real.
    """
    _assert_table(table)
    if not public_id or len(public_id) > 64:
        raise ResourceNotFoundError()

    sql = (
        f"SELECT {columns} FROM public.{table} "  # noqa: S608 - table is allowlisted
        f"WHERE public_id = :pid AND company_id = :cid"
    )
    if not include_deleted and table not in _NO_SOFT_DELETE:
        sql += " AND deleted_at IS NULL"
    if lock:
        sql += " FOR UPDATE"

    row = (await conn.execute(text(sql), {"pid": public_id, "cid": company_id})).mappings().first()
    if row is None:
        raise ResourceNotFoundError()
    return dict(row)


async def resolve_by_id(
    conn: AsyncConnection,
    table: str,
    row_id: uuid.UUID,
    company_id: uuid.UUID,
    *,
    columns: str = "*",
    lock: bool = False,
    include_deleted: bool = False,
) -> dict[str, Any]:
    """Same as `resolve_scoped` but keyed by the internal uuid."""
    _assert_table(table)
    sql = (
        f"SELECT {columns} FROM public.{table} "  # noqa: S608 - table is allowlisted
        f"WHERE id = :rid AND company_id = :cid"
    )
    if not include_deleted and table not in _NO_SOFT_DELETE:
        sql += " AND deleted_at IS NULL"
    if lock:
        sql += " FOR UPDATE"

    row = (await conn.execute(text(sql), {"rid": row_id, "cid": company_id})).mappings().first()
    if row is None:
        raise ResourceNotFoundError()
    return dict(row)


async def exists_in_company(
    conn: AsyncConnection,
    table: str,
    row_id: uuid.UUID,
    company_id: uuid.UUID,
    *,
    include_deleted: bool = False,
) -> bool:
    _assert_table(table)
    sql = f"SELECT 1 FROM public.{table} WHERE id = :rid AND company_id = :cid"  # noqa: S608
    if not include_deleted and table not in _NO_SOFT_DELETE:
        sql += " AND deleted_at IS NULL"
    return bool((await conn.execute(text(sql), {"rid": row_id, "cid": company_id})).scalar())


async def resolve_user_public_id(
    conn: AsyncConnection, public_id: str, *, company_id: uuid.UUID | None = None
) -> uuid.UUID:
    """Resolve a `U...` identifier, optionally requiring company membership.

    Company membership is checked explicitly because `users` has no RLS policy of
    its own — identity is a global namespace, membership is what scopes it.
    """
    if not public_id or not public_id.startswith("U"):
        raise ResourceNotFoundError("User not found.")

    row = (
        (
            await conn.execute(
                text("SELECT id::text FROM public.users WHERE public_id = :pid"),
                {"pid": public_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ResourceNotFoundError("User not found.")

    user_id = uuid.UUID(str(row["id"]))
    if company_id is not None:
        member = await conn.execute(
            text("SELECT app.is_member(CAST(:cid AS uuid), CAST(:uid AS uuid))"),
            {"cid": company_id, "uid": user_id},
        )
        if not member.scalar():
            raise ResourceNotFoundError("User is not a member of this company.")
    return user_id


async def resolve_by_public(
    conn: AsyncConnection,
    table: str,
    public_id: str,
    company_id: uuid.UUID,
) -> uuid.UUID:
    """Resolve a public id to its uuid when only the id (not the row) is needed.

    Used by polymorphic references such as `documents.related_id`, where the
    service needs the uuid but not the payload.
    """
    row = await resolve_scoped(conn, table, public_id, company_id, columns="id")
    return uuid.UUID(str(row["id"]))


async def resolve_company_public_id(conn: AsyncConnection, public_id: str) -> uuid.UUID:
    """Resolve a `CO...` company identifier to its uuid.

    Used for counterparty references, which legitimately point at a *different*
    company, so no membership test applies here.
    """
    if not public_id or not public_id.startswith("CO"):
        raise ResourceNotFoundError("Company not found.")
    row = (
        (
            await conn.execute(
                text(
                    "SELECT id::text FROM public.companies"
                    " WHERE public_id = :pid AND deleted_at IS NULL"
                ),
                {"pid": public_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ResourceNotFoundError("Company not found.")
    return uuid.UUID(str(row["id"]))


def as_decimal(value: Any, default: str = "0") -> Any:
    from decimal import Decimal, InvalidOperation

    if value is None:
        return Decimal(default)
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal(default)


def json_or_empty(value: Any) -> Any:
    if value is None:
        return {}
    if isinstance(value, str):
        import json

        try:
            return json.loads(value)
        except ValueError:
            return {}
    return value


def apply_status_filter(
    where: list[str], params: dict[str, Any], column: str, status: str | None
) -> None:
    """One status or a comma-separated set, for tab bars over list views.

    Values are upper-cased to the enum vocabulary; unknown values simply match
    nothing rather than erroring, so a tab can never break the list. The
    column is always a caller-side literal, never request input.
    """
    if not status:
        return
    values = [part.strip().upper() for part in str(status).split(",") if part.strip()]
    if not values:
        return
    if len(values) == 1:
        where.append(column + " = :status")
        params["status"] = values[0]
    else:
        where.append(column + " = ANY(CAST(:statuses AS text[]))")
        params["statuses"] = values
