"""Shared fixtures for the MyTrakin API test suite.

Two kinds of test live here:

* pure-unit tests, which need nothing at all;
* database-backed tests, which need the real PostgreSQL instance configured in
  the repository-root ``.env``.

Everything database-backed shares this module rather than repeating the
bootstrap, for one reason: the fixture must be the only thing that writes rows.
Two rules keep the database clean between runs.

1. ``tenants`` (session scope) creates the users, companies and memberships and
   deletes the companies — and with them, everything that cascades — plus the
   users when the session ends.
2. ``conn`` (function scope) hands out one transaction per test and rolls it
   back, so a test cannot leak rows into the next one. ``public.bank_transactions``
   is append-only and refuses DELETE outright, so a bank transaction may only
   ever be created inside a transaction that is later rolled back.

``.env`` is loaded inside the ``_environment`` fixture rather than at import
time. Module-level code in other test modules reads ``os.environ`` while they
are being collected, which happens before any fixture runs; loading here keeps
that behaviour unchanged.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

ROOT = Path(__file__).resolve().parents[3]

# psycopg's async driver cannot run on the Windows proactor loop. Importing
# `app.db.session` installs the selector policy (via `app.core.event_loop`) as a
# side effect, and it has to happen at import time: pytest-asyncio builds the
# event loop for the first async fixture, and the policy must already be in place
# by then. Every other fixture in this suite opens a psycopg connection.
import app.db.session  # noqa: E402,F401  (imported for its side effect)

# `scripts/load_env.py` reads the repository-root .env. Importing it needs the
# directory on sys.path first; the venv does not have the repo installed.
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

#: Emails are tagged so a leaked fixture row is identifiable in psql.
#: Emails are tagged so a leaked fixture row is identifiable in psql. The leading
#: "@" is part of the constant so callers cannot forget it.
TEST_EMAIL_DOMAIN = "@svc-fixture.test"

#: The role the API is *intended* to run as. It is neither superuser nor
#: BYPASSRLS, so RLS policies and the `app.is_trusted_context()` guards inside the
#: trigger functions are actually evaluated when a test impersonates it. The
#: connection in DATABASE_URL is a BYPASSRLS role, which short-circuits those
#: guards, so the tests that assert database-level enforcement (segregation of
#: duties, public_id immutability) impersonate this one explicitly.
API_ROLE = "mytrakin_api"


@dataclass(frozen=True, slots=True)
class Tenant:
    """One provisioned user, one company, and the membership joining them."""

    key: str
    user_id: uuid.UUID
    user_public_id: str
    company_id: uuid.UUID
    company_public_id: str

    def public_id_for(self, user_id: uuid.UUID) -> str:
        return self.user_public_id if user_id == self.user_id else ""


def _run(coro: Any) -> Any:
    """Run one coroutine on a private loop and close it again.

    The session fixture is synchronous, but ``provision_user`` is async and the
    async engine's pool is bound to the loop that first used it. Running the
    setup on a throwaway loop and then disposing the engine leaves the pool empty
    for the function-scoped fixtures, which run on pytest-asyncio's own loop.
    """
    from app.core.event_loop import ensure_compatible_event_loop

    ensure_compatible_event_loop()
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        asyncio.set_event_loop(None)
        loop.close()


@pytest.fixture(scope="session", autouse=True)
def _environment() -> Iterator[None]:
    """Load the repository-root .env into os.environ, once per session."""
    import load_env

    for key, value in load_env.load().items():
        os.environ.setdefault(key, value)
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("DATABASE_URL is not set; the repository-root .env is unreadable")
    yield


@pytest.fixture(scope="session")
def tenants(_environment: None) -> dict[str, Tenant]:
    """Two users and two companies, provisioned once for the whole session.

    ``app.core.security.provision_user`` creates the ``public.users`` row (and
    its profile and notification preferences) from a Supabase auth uuid. Both
    companies are inserted with ``public_id`` left NULL so the
    ``*_public_id`` trigger allocates the ``CO...`` identifier, and
    ``app.bootstrap_company_roles`` creates the role templates plus one
    ``SUPER_ADMIN`` membership per company.
    """
    import psycopg

    from app.core.security import provision_user
    from app.db.session import dispose_engine, get_connection_factory, set_identity

    marker = uuid.uuid4().hex[:8]

    async def build() -> dict[str, Tenant]:
        factory = get_connection_factory()
        async with factory() as conn:
            tx = await conn.begin()
            try:
                users: dict[str, uuid.UUID] = {}
                for key in ("admin", "worker"):
                    users[key] = uuid.UUID(
                        await provision_user(
                            conn,
                            str(uuid.uuid4()),
                            email=f"{key}_{marker}{TEST_EMAIL_DOMAIN}",
                            first_name=key.title(),
                            # Fixtures simulate Supabase-confirmed users; real
                            # signups start PENDING_VERIFICATION instead.
                            verified=True,
                        )
                    )

                user_rows = (
                    (
                        await conn.execute(
                            text(
                                "SELECT id::text, public_id FROM public.users"
                                " WHERE id = ANY(CAST(:ids AS uuid[]))"
                            ),
                            {"ids": [str(u) for u in users.values()]},
                        )
                    )
                    .mappings()
                    .all()
                )
                public_ids = {r["id"]: r["public_id"] for r in user_rows}
                companies: dict[str, dict[str, str]] = {}
                for key, founder in (("alpha", users["admin"]), ("beta", users["worker"])):
                    row = (
                        (
                            await conn.execute(
                                text(
                                    "INSERT INTO public.companies"
                                    " (legal_name, display_name, created_by)"
                                    " VALUES (:legal, :display, :by)"
                                    " RETURNING id::text, public_id"
                                ),
                                {
                                    "legal": f"{key.title()} Legal {marker}",
                                    "display": f"{key.title()} {marker}",
                                    "by": str(founder),
                                },
                            )
                        )
                        .mappings()
                        .one()
                    )
                    companies[key] = {"id": row["id"], "public_id": row["public_id"]}
                    await conn.execute(
                        text(
                            "SELECT app.bootstrap_company_roles("
                            "  CAST(:company AS uuid), CAST(:founder AS uuid))"
                        ),
                        {"company": row["id"], "founder": str(founder)},
                    )

                out: dict[str, Tenant] = {}
                for key, company_key in (("admin", "alpha"), ("worker", "beta")):
                    out[key] = Tenant(
                        key=key,
                        user_id=users[key],
                        user_public_id=public_ids[str(users[key])],
                        company_id=uuid.UUID(companies[company_key]["id"]),
                        company_public_id=companies[company_key]["public_id"],
                    )

                # A second SUPER_ADMIN inside company A. Two people are needed for
                # the four-eyes rules the database enforces (a timesheet cannot be
                # approved by its author; an invoice cannot be approved by whoever
                # generated it), and the required "one membership each" rows are
                # the ones bootstrap_company_roles just wrote.
                await conn.execute(
                    text(
                        """
                        INSERT INTO public.company_memberships
                          (company_id, user_id, role_id, status, joined_at)
                        SELECT CAST(:company AS uuid), CAST(:user AS uuid), id,
                               'ACTIVE', now()
                          FROM public.company_roles
                         WHERE company_id = CAST(:company AS uuid) AND key = 'SUPER_ADMIN'
                        ON CONFLICT (company_id, user_id) DO NOTHING
                        """
                    ),
                    {
                        "company": out["admin"].company_id,
                        "user": out["worker"].user_id,
                    },
                )
                await set_identity(
                    conn,
                    user_id=out["admin"].user_id,
                    company_id=out["admin"].company_id,
                    request_id="fixture",
                )
                await tx.commit()
                return out
            except BaseException:
                await tx.rollback()
                raise

    built = _run(build())
    _run(dispose_engine())

    dsn = os.environ["DATABASE_URL"]
    with psycopg.connect(dsn, autocommit=True, prepare_threshold=None) as admin:
        admin.execute(f"GRANT {API_ROLE} TO CURRENT_USER")
    try:
        yield built
    finally:
        # CASCADE from companies removes every child row (projects, contracts,
        # invoices, timesheets, ...). The RESTRICT foreign keys that protect
        # contracts/invoices/payments from being the *referenced* side are all
        # owned by company A, so company A must go first: deleting A cascades
        # its own contracts and unblocks B.
        with psycopg.connect(dsn, autocommit=True, prepare_threshold=None) as admin:
            admin.execute(
                "DELETE FROM public.companies WHERE id = ANY(%s)",
                ([str(t.company_id) for t in built.values()],),
            )
            admin.execute(
                "DELETE FROM public.users WHERE id = ANY(%s)",
                ([str(t.user_id) for t in built.values()],),
            )


@pytest_asyncio.fixture
async def conn(tenants: dict[str, Tenant]) -> AsyncIterator[AsyncConnection]:
    """One transaction per test, carrying company A's identity.

    Rolled back on the way out, so a test leaves nothing behind even when it
    fails part-way through.
    """
    from app.db.session import get_connection_factory, set_identity

    tenant = tenants["admin"]
    factory = get_connection_factory()
    async with factory() as connection:
        tx = await connection.begin()
        try:
            await set_identity(
                connection,
                user_id=tenant.user_id,
                company_id=tenant.company_id,
                request_id="pytest",
            )
            yield connection
        finally:
            await tx.rollback()


@pytest_asyncio.fixture
async def api_conn(tenants: dict[str, Tenant]) -> AsyncIterator[AsyncConnection]:
    """Like ``conn``, but impersonating the non-BYPASSRLS ``mytrakin_api`` role.

    Several trigger functions begin with ``IF app.is_trusted_context() THEN
    RETURN NEW``, and that helper is true for any role with BYPASSRLS — which
    includes the connection in ``DATABASE_URL``. Dropping to ``mytrakin_api``
    inside the transaction is what makes those guards, and therefore RLS,
    genuinely run.
    """
    from app.db.session import get_connection_factory, set_identity

    tenant = tenants["admin"]
    factory = get_connection_factory()
    async with factory() as connection:
        tx = await connection.begin()
        try:
            await connection.execute(text(f"SET LOCAL ROLE {API_ROLE}"))
            await set_identity(
                connection,
                user_id=tenant.user_id,
                company_id=tenant.company_id,
                request_id="pytest-api-role",
            )
            yield connection
        finally:
            await tx.rollback()


@pytest_asyncio.fixture
async def other_tenant_conn(tenants: dict[str, Tenant]) -> AsyncIterator[AsyncConnection]:
    """Company B's identity, for proving a cross-tenant read returns nothing."""
    from app.db.session import get_connection_factory, set_identity

    tenant = tenants["worker"]
    factory = get_connection_factory()
    async with factory() as connection:
        tx = await connection.begin()
        try:
            await set_identity(
                connection,
                user_id=tenant.user_id,
                company_id=tenant.company_id,
                request_id="pytest-other",
            )
            yield connection
        finally:
            await tx.rollback()


@pytest_asyncio.fixture
async def act_as(conn: AsyncConnection):
    """``act_as(user, company)`` re-points the session identity mid-test.

    Mirrors ``app.db.session.set_identity``: ``SET LOCAL`` values are scoped to
    the transaction, so switching and switching back cannot leak.
    """
    from app.db.session import set_identity

    async def _act_as(user_id: uuid.UUID, company_id: uuid.UUID) -> None:
        await set_identity(conn, user_id=user_id, company_id=company_id, request_id="pytest")

    return _act_as


# =============================================================================
# A minimal commercial chain, for tests that need parents before they can
# exercise a child.
# =============================================================================


class Skeleton:
    """The parent rows almost every tenant-scoped table needs.

    Held as bare internal uuids: the tables under test are addressed by
    ``public_id`` in the service layer and by ``id`` in the trigger tests.
    """

    def __init__(self) -> None:
        self.project = ""
        self.project_role = ""
        self.sow = ""
        self.contract = ""
        self.contract_role = ""
        self.assignment = ""
        self.leave_policy = ""
        self.bank_connection = ""


async def build_skeleton(
    conn: AsyncConnection, tenants: dict[str, Tenant], *, untrusted: bool = False
) -> Skeleton:
    """One project -> role -> SOW -> contract -> contract role -> assignment chain.

    Written with direct SQL. That is deliberate: a fixture that reached the same
    state through `app.services` could not, because several service reads are
    broken against this schema (see the defect list in the report). Using SQL
    here keeps the fixture honest about *what* is being tested: the database.

    ``untrusted=True`` inserts the bank connection as the session role and drops
    straight back to ``mytrakin_api``. The migration grants no INSERT at all on
    ``public.bank_connections`` to ``mytrakin_api`` — itself a reported defect —
    so that one parent row has to be written by a role that can.
    """
    from app.core.clock import utc_today

    tenant = tenants["admin"]
    out = Skeleton()
    today = utc_today()
    params: dict[str, Any] = {
        "c": tenant.company_id,
        "cp": tenants["worker"].company_id,
        "u": tenant.user_id,
        "u2": tenants["worker"].user_id,
        "today": today,
        "ed": today + timedelta(days=300),
        # The assignment reaches back far enough for a timesheet period that has
        # already closed; app.can_record_time refuses a period it does not cover.
        "started": today - timedelta(days=60),
    }

    async def one(sql: str) -> str:
        return str((await conn.execute(text(sql), params)).scalar_one())

    out.project = await one(
        "INSERT INTO public.projects (company_id, name, status)"
        " VALUES (:c, 'Test project', 'ACTIVE') RETURNING id"
    )
    params["p"] = out.project
    out.project_role = await one(
        "INSERT INTO public.project_roles (project_id, company_id, title, required_count)"
        " VALUES (:p, :c, 'Test role', 5) RETURNING id"
    )
    params["role"] = out.project_role
    out.sow = await one(
        "INSERT INTO public.sows (project_id, company_id, sow_type, counterparty_company_id,"
        " title, status, start_date, end_date)"
        " VALUES (:p, :c, 'COMPANY', :cp, 'Test SOW', 'ACTIVE', :today, :ed) RETURNING id"
    )
    params["s"] = out.sow
    out.contract = await one(
        "INSERT INTO public.contracts (sow_id, project_id, company_id, contract_type,"
        " counterparty_company_id, title, status, start_date, end_date)"
        " VALUES (:s, :p, :c, 'COMPANY', :cp, 'Test contract', 'ACTIVE', :today, :ed)"
        " RETURNING id"
    )
    params["ct"] = out.contract
    out.contract_role = await one(
        "INSERT INTO public.contract_roles (contract_id, project_role_id, quantity, rate,"
        " rate_type, currency)"
        " VALUES (:ct, :role, 1, 75, 'HOURLY', 'USD') RETURNING id"
    )
    params["cr"] = out.contract_role
    out.assignment = await one(
        "INSERT INTO public.assignments (contract_id, contract_role_id, project_id, company_id,"
        " user_id, role_title, hourly_rate, currency, start_date, end_date, status)"
        " VALUES (:ct, :cr, :p, :c, :u2, 'Test role', 75, 'USD', :started, :ed, 'ACTIVE')"
        " RETURNING id"
    )
    params["asg"] = out.assignment
    out.leave_policy = await one(
        "INSERT INTO public.leave_policies (company_id, name, leave_type, accrual_method,"
        " effective_from) VALUES (:c, 'Test leave policy', 'ANNUAL', 'MONTHLY', :today)"
        " RETURNING id"
    )
    params["lp"] = out.leave_policy
    if untrusted:
        await conn.execute(text("SET LOCAL ROLE NONE"))
    out.bank_connection = await one(
        "INSERT INTO public.bank_connections (company_id, owner_user_id, institution_name,"
        " institution_id, item_id_encrypted, access_token_encrypted, status)"
        " VALUES (:c, :u, 'Test bank', 'ins_test', decode('00','hex'),"
        " decode('00','hex'), 'CONNECTED') RETURNING id"
    )
    if untrusted:
        await conn.execute(text(f"SET LOCAL ROLE {API_ROLE}"))
    params["bc"] = out.bank_connection
    return out


@pytest.fixture
async def skeleton(conn: AsyncConnection, tenants: dict[str, Tenant]) -> Skeleton:
    return await build_skeleton(conn, tenants)


@pytest.fixture
async def api_skeleton(api_conn: AsyncConnection, tenants: dict[str, Tenant]) -> Skeleton:
    """The same chain, built inside a transaction that dropped to mytrakin_api."""
    return await build_skeleton(api_conn, tenants, untrusted=True)
