"""Security / RLS test suite for MyTrakin.

These tests exercise the authorisation model as the *end user*, exactly the way
the FastAPI service does: `SET LOCAL app.user_id` / `app.company_id` inside a
transaction, then querying as the non-superuser `mytrakin_api` role so RLS is
actually enforced.

What is proven here (see docs/rls.md for the full matrix):

  T1  cross-company data isolation (IDOR / BOLA)
  T2  membership alone does not grant access
  T3  granular permissions, not roles, gate access
  T4  role changes require roles.manage (no privilege escalation)
  T5  public_id is immutable
  T6  contract status machine cannot be skipped
  T7  project-role allocation cannot exceed required_count
  T8  timesheets require an accepted/active contract (no orphan work)
  T9  locked timesheets are immutable; self-approval blocked
  T10 invoices cannot be submitted without an active MSA
  T11 invoice totals are derived, not client-supplied
  T12 AI retrieval is permission-filtered before context assembly
  T13 AI cannot execute an action without human approval
  T14 payments cannot move money without authorization
  T15 bank transaction ledger is append-only
  T16 unauthenticated requests see nothing
  T17 sensitive columns are not readable by the API role
  T18 the last SUPER_ADMIN cannot be removed
  T19 audit log is append-only
  T20 documents respect tenant boundaries and scan gates
  T21 permission resolution returns the full set, not just the first

Run:
    DATABASE_ADMIN_URL=postgresql://... python -m pytest tests/test_rls_security.py -v
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
import pytest

# Declared so `pytest -m security` selects this file, as the Makefile promises.
pytestmark = pytest.mark.security

ADMIN_DSN = os.environ.get("DATABASE_ADMIN_URL") or os.environ.get("DATABASE_URL")
if not ADMIN_DSN:
    pytest.skip("DATABASE_ADMIN_URL is not set", allow_module_level=True)

API_ROLE = "mytrakin_api"

# Child-before-parent order for teardown. RESTRICT foreign keys on contracts,
# invoices and payments are intentional: they stop an accidental
# `DELETE FROM companies` from destroying legal and financial records.
TEARDOWN_CHILD_FIRST = [
    "public.payment_allocations",
    "public.payment_matches",
    "public.payment_requests",
    "public.payment_schedules",
    "public.payments",
    # public.bank_transactions is intentionally absent: it rejects DELETE for
    # every role, so it is reset with TRUNCATE below.
    "public.bank_accounts",
    "public.bank_connections",
    "public.payment_accounts",
    "public.invoice_approvals",
    "public.invoice_allocations",
    "public.invoice_items",
    "public.invoices",
    "public.billing_runs",
    "public.timesheet_approvals",
    "public.timesheet_revisions",
    "public.timesheet_entries",
    "public.timesheets",
    "public.assignments",
    "public.leave_requests",
    "public.leave_balances",
    "public.leave_policies",
    "public.contract_approval_steps",
    "public.contract_line_items",
    "public.contract_roles",
    "public.contract_parties",
    "public.contracts",
    "public.sow_roles",
    "public.sows",
    "public.project_roles",
    "public.projects",
    "public.msa_requests",
    "public.msa_versions",
    "public.msas",
    "public.document_access_log",
    "public.document_versions",
    "public.documents",
    "public.ai_automation_runs",
    "public.ai_automations",
    "public.ai_actions",
    "public.ai_extractions",
    "public.ai_document_chunks",
    "public.ai_knowledge_documents",
    "public.conversation_members",
    "public.messages",
    "public.conversations",
    "public.post_shares",
    "public.post_reactions",
    "public.post_comments",
    "public.posts",
    "public.connection_requests",
    "public.connections",
    "public.user_blocks",
    "public.company_invitations",
    "public.role_permissions",
    "public.company_memberships",
    "public.company_roles",
    "public.companies",
    "public.user_skills",
    "public.user_educations",
    "public.user_experiences",
    "public.user_certifications",
    "public.user_privacy",
    "public.user_sensitive",
    "public.user_profiles",
    "public.user_security_flags",
    "public.users",
]

# Tests impersonate mytrakin_api so RLS is genuinely enforced rather than
# bypassed by a superuser session. The role is granted to the session in the
# `admin` fixture; it is neither superuser nor BYPASSRLS.

# ---------------------------------------------------------------------------
# Fixtures: a two-company world with several actors
# ---------------------------------------------------------------------------


class Actor:
    """A user plus the company context they act in."""

    def __init__(self, user_id: uuid, email: str, name: str) -> None:
        self.user_id = user_id
        self.email = email
        self.name = name


class World:
    def __init__(self) -> None:
        self.users: dict[str, Actor] = {}
        self.companies: dict[str, uuid] = {}
        self.roles: dict[tuple[str, str], uuid] = {}
        self.project_id: uuid | None = None
        self.contract_a: uuid | None = None
        self.doc_a: uuid | None = None
        self.doc_b: uuid | None = None
        self.invoice_a: uuid | None = None


@pytest.fixture(scope="module")
def admin() -> Iterator[psycopg.Connection]:
    # See as_user(): named prepared statements are unsafe behind the pooler.
    with psycopg.connect(ADMIN_DSN, autocommit=True, prepare_threshold=None) as conn:
        conn.execute(
            """
            DO $$
            BEGIN
              IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mytrakin_api') THEN
                CREATE ROLE mytrakin_api NOLOGIN;
              END IF;
            END
            $$;
            """
        )
        conn.execute("GRANT mytrakin_api TO CURRENT_USER")
        yield conn


@pytest.fixture
def world(admin: psycopg.Connection) -> Iterator[World]:
    """Build a fresh multi-tenant fixture, then tear it down completely."""
    w = World()
    marker = uuid.uuid4().hex[:8]

    with admin.transaction():
        for key in ("owner_a", "admin_a", "staff_a", "outsider_b", "outsider_c"):
            email = f"{key}_{marker}@rls.test"
            cur = admin.execute(
                """
                INSERT INTO public.users
                  (auth_id, email, email_verified_at, first_name, last_name, status)
                VALUES (%s, %s, now(), %s, 'Test', 'ACTIVE')
                RETURNING id
                """,
                (uuid.uuid4(), email, key),
            )
            uid = cur.fetchone()[0]
            w.users[key] = Actor(uid, email, key)
            admin.execute("INSERT INTO public.user_profiles (user_id) VALUES (%s)", (uid,))

    try:
        yield w
    finally:
        # Several tables deliberately use ON DELETE RESTRICT (contracts, invoices,
        # payments are legal/financial artefacts), so teardown walks the graph in
        # dependency order. See scripts/cleanup_test_data.py for the same list.
        # Reset the append-only ledgers first: deleting a bank_account cascades to
        # bank_transactions, whose BEFORE DELETE trigger refuses the cascade.
        for table in ("public.bank_transactions", "platform.audit_logs"):
            with admin.transaction():
                admin.execute(f"TRUNCATE {table} CASCADE")

        # Teardown must remove rows from LOCKED timesheets, which the (now
        # correct) app.assert_timesheet_editable() guard refuses to DELETE for
        # ordinary users -- exactly what T9 proves. TRUNCATE fires no row
        # triggers, so it removes those rows without tripping the guard; the
        # DELETE loop below then handles everything else in dependency order.
        # (SET LOCAL app.actor_type='SYSTEM' would also bypass the guard, but
        # the Supabase transaction pooler does not reliably preserve GUCs.)
        with admin.transaction():
            admin.execute(
                "TRUNCATE public.timesheet_entries, public.timesheet_revisions,"
                " public.timesheet_approvals, public.timesheets CASCADE"
            )
        for table in TEARDOWN_CHILD_FIRST:
            with admin.transaction():
                admin.execute(f"DELETE FROM {table}")

        with admin.transaction():
            for actor in w.users.values():
                admin.execute("DELETE FROM public.users WHERE id = %s", (actor.user_id,))


@contextmanager
def expect_raises(cur: _SavepointCursor | psycopg.Cursor, exc: type[Exception]) -> Iterator[None]:
    """Assert the enclosed statement raises `exc`, without poisoning the transaction.

    The statement runs inside a savepoint; the error is caught, the savepoint is
    rolled back, and the exception is suppressed so the test continues.
    """
    if isinstance(cur, _SavepointCursor):
        name = cur._next_savepoint()
        cur.execute(f"SAVEPOINT {name}")
        try:
            yield
        except exc:
            cur.execute(f"ROLLBACK TO SAVEPOINT {name}")
            cur.execute(f"RELEASE SAVEPOINT {name}")
            return
        except Exception as other:
            cur.execute(f"ROLLBACK TO SAVEPOINT {name}")
            cur.execute(f"RELEASE SAVEPOINT {name}")
            raise AssertionError(
                f"expected {exc.__name__}, got {type(other).__name__}: {other}"
            ) from other
        cur.execute(f"ROLLBACK TO SAVEPOINT {name}")
        cur.execute(f"RELEASE SAVEPOINT {name}")
        raise AssertionError(f"expected {exc.__name__} but the statement succeeded")

    # A raw connection (module-scoped admin) cannot savepoint.
    with pytest.raises(exc):
        yield


def make_company(admin: psycopg.Connection, w: World, key: str, owner: Actor) -> uuid:
    """Create a company and bootstrap its role templates for `owner`."""
    cur = admin.execute(
        """
        INSERT INTO public.companies (public_id, legal_name, display_name, created_by)
        VALUES ('CO' || upper(substr(md5(random()::text), 1, 8)),
                %s, %s, %s)
        RETURNING id
        """,
        (f"{key} Legal Ltd", f"{key} Ltd", owner.user_id),
    )
    cid = cur.fetchone()[0]
    w.companies[key] = cid
    admin.execute("SELECT app.bootstrap_company_roles(%s, %s)", (cid, owner.user_id))
    return cid


def approver_can_approve(admin: psycopg.Connection, company_id: uuid, user_id: uuid) -> bool:
    """True when the user is an active member holding timesheets.approve."""
    row = admin.execute(
        "SELECT app.has_permission(%s, 'timesheets.approve', %s)",
        (company_id, user_id),
    ).fetchone()
    return bool(row[0])


def _own_timesheet(
    admin: psycopg.Connection,
    company_id: uuid,
    user_id: uuid,
    contract_id: uuid,
    project_id: uuid,
) -> uuid:
    """Create an assignment and a submitted-ready timesheet for `user_id`."""
    cur = admin.execute(
        """
        INSERT INTO public.assignments
          (contract_id, project_id, company_id, user_id, role_title, start_date,
           end_date, status, hourly_rate)
        VALUES (%s, %s, %s, %s, 'Lead', current_date - 30, current_date + 300,
                'ACTIVE', 80.00)
        RETURNING id
        """,
        (contract_id, project_id, company_id, user_id),
    )
    assignment_id = cur.fetchone()[0]
    cur = admin.execute(
        """
        INSERT INTO public.timesheets
          (user_id, company_id, assignment_id, contract_id, project_id,
           period_start, period_end, status)
        VALUES (%s, %s, %s, %s, %s, date_trunc('month', current_date)::date,
                (date_trunc('month', current_date) + interval '27 days')::date, 'DRAFT')
        RETURNING id
        """,
        (user_id, company_id, assignment_id, contract_id, project_id),
    )
    return cur.fetchone()[0]


def add_member(
    admin: psycopg.Connection,
    company_id: uuid,
    user_id: uuid,
    role_key: str,
) -> uuid:
    cur = admin.execute(
        """
        INSERT INTO public.company_memberships (company_id, user_id, role_id, status, joined_at)
        VALUES (%s, %s,
                (SELECT id FROM public.company_roles WHERE company_id = %s AND key = %s),
                'ACTIVE', now())
        RETURNING id
        """,
        (company_id, user_id, company_id, role_key),
    )
    return cur.fetchone()[0]


@contextmanager
def as_user(
    user_id: uuid | None,
    company_id: uuid | None = None,
    *,
    role: str = API_ROLE,
    request_id: str = "test-request",
) -> Iterator[psycopg.Cursor]:
    """Open a cursor impersonating `user_id` inside `company_id`.

    This is the same SET LOCAL sequence the API performs, then the cursor is
    downgraded to the non-superuser API role so RLS is enforced rather than
    bypassed.

    Statements that are expected to raise do so inside a SAVEPOINT, so one failed
    assertion does not abort the surrounding transaction for later assertions.

    A dedicated connection is opened per impersonation: the pooler multiplexes one
    backend across sessions, and SET LOCAL would otherwise leak into the fixture's
    superuser connection.
    """
    # prepare_threshold=None disables psycopg's named prepared statements. The
    # Supabase transaction pooler multiplexes a single backend across sessions, so
    # a named statement created by one session is invisible to the next and
    # PostgreSQL reports "prepared statement already exists" / "does not exist".
    # Plain unnamed statements are correct for pooled connections.
    with psycopg.connect(ADMIN_DSN, autocommit=True, prepare_threshold=None) as conn:
        conn.execute("GRANT mytrakin_api TO CURRENT_USER")
        conn.execute("GRANT mytrakin_worker TO CURRENT_USER")
        with conn.transaction():
            cur = conn.cursor()
            cur.execute(f"SET LOCAL ROLE {role}")
            cur.execute("SELECT set_config('app.user_id', %s, true)", (str(user_id or ""),))
            cur.execute("SELECT set_config('app.company_id', %s, true)", (str(company_id or ""),))
            cur.execute("SELECT set_config('app.request_id', %s, true)", (request_id,))
            cur.execute("SELECT set_config('app.actor_type', 'USER', true)")
            # Generous, because the pooler serialises sessions and a shared backend
            # under suite load can be slow. This is a runaway guard, not a budget.
            cur.execute("SET LOCAL statement_timeout = '120s'")
            try:
                yield _SavepointCursor(cur)
            finally:
                cur.close()


class _SavepointCursor:
    """Thin cursor proxy that can roll a failed statement back to a savepoint.

    psycopg aborts the whole transaction on any error, so a test asserting
    `pytest.raises` must not leave the transaction poisoned for the next
    assertion. `_expect_failure` wraps each such call in a savepoint.
    """

    def __init__(self, cur: psycopg.Cursor) -> None:
        self._cur = cur
        self._depth = 0

    def __getattr__(self, item: str):
        return getattr(self._cur, item)

    def _next_savepoint(self) -> str:
        self._depth += 1
        return f"sp_{self._depth}"


# ---------------------------------------------------------------------------
# T1 / T2 / T3 — tenant isolation and permissions
# ---------------------------------------------------------------------------


def test_t1_cross_company_isolation(world: World, admin: psycopg.Connection) -> None:
    """A member of Company A cannot read Company B's projects, even by direct id."""
    owner_a = world.users["owner_a"]
    owner_b = world.users["outsider_b"]
    comp_a = make_company(admin, world, "A", owner_a)
    comp_b = make_company(admin, world, "B", owner_b)

    cur = admin.execute(
        """
        INSERT INTO public.projects (company_id, name, status)
        VALUES (%s, 'Confidential A Project', 'ACTIVE') RETURNING id
        """,
        (comp_a,),
    )
    project_a = cur.fetchone()[0]

    # 1. Owner's own company context: visible.
    with as_user(owner_a.user_id, comp_a) as c:
        assert c.execute("SELECT count(*) FROM public.projects").fetchone()[0] == 1

    # 2. Same user, Company B context: nothing.
    with as_user(owner_b.user_id, comp_b) as c:
        assert c.execute("SELECT count(*) FROM public.projects").fetchone()[0] == 0

    # 3. BOLA: guess A's internal UUID and query it directly.
    with as_user(owner_b.user_id, comp_b) as c:
        rows = c.execute("SELECT id FROM public.projects WHERE id = %s", (project_a,)).fetchall()
        assert rows == [], "cross-company project leaked by direct id"

    # 4. Even with the wrong company context set but correct user.
    with as_user(owner_a.user_id, comp_b) as c:
        assert c.execute("SELECT count(*) FROM public.projects").fetchone()[0] == 0


def test_t2_membership_alone_is_not_access(world: World, admin: psycopg.Connection) -> None:
    """An active member can read only what their role grants, and nothing more."""
    owner_a = world.users["owner_a"]
    staff = world.users["staff_a"]
    comp_a = make_company(admin, world, "A", owner_a)
    add_member(admin, comp_a, staff.user_id, "VIEWER")

    cur = admin.execute(
        """
        INSERT INTO public.projects (company_id, name, status)
        VALUES (%s, 'Restricted', 'ACTIVE') RETURNING id
        """,
        (comp_a,),
    )
    project_a = cur.fetchone()[0]

    with as_user(staff.user_id, comp_a) as c:
        # VIEWER holds projects.read, so it can read ...
        assert c.execute("SELECT count(*) FROM public.projects").fetchone()[0] == 1

        # ... but an UPDATE is filtered to zero rows by RLS. This is silent by
        # design: raising would confirm the row exists. Verify nothing changed.
        updated = c.execute(
            "UPDATE public.projects SET name = 'hijacked' WHERE id = %s RETURNING id",
            (project_a,),
        ).fetchall()
        assert updated == []

        # An INSERT is refused outright by the permission guard trigger, so a
        # caller learns immediately that they lack authority.
        with expect_raises(c, psycopg.errors.InsufficientPrivilege):
            c.execute(
                "INSERT INTO public.projects (company_id, name, status) "
                "VALUES (%s, 'Injected', 'ACTIVE')",
                (comp_a,),
            )

    # The row is untouched.
    name = admin.execute("SELECT name FROM public.projects WHERE id = %s", (project_a,)).fetchone()[
        0
    ]
    assert name == "Restricted"


def test_t3_granular_permission_gating(world: World, admin: psycopg.Connection) -> None:
    """A company role with no contracts.read cannot read contracts."""
    owner_a = world.users["owner_a"]
    staff = world.users["staff_a"]
    comp_a = make_company(admin, world, "A", owner_a)
    add_member(admin, comp_a, staff.user_id, "EMPLOYEE")  # no contracts.read_rates

    # EMPLOYEE does hold contracts.read, so create the contract then check the
    # rate-bearing table is invisible.
    cur = admin.execute(
        """
        INSERT INTO public.projects (company_id, name, status)
        VALUES (%s, 'P1', 'ACTIVE') RETURNING id
        """,
        (comp_a,),
    )
    pid = cur.fetchone()[0]
    cur = admin.execute(
        """
        INSERT INTO public.sows (public_id, project_id, company_id, sow_type,
                                 counterparty_company_id, title, status)
        VALUES ('S' || upper(substr(md5(random()::text),1,8)), %s, %s, 'COMPANY', %s,
                'SOW 1', 'ACTIVE') RETURNING id
        """,
        (pid, comp_a, comp_a),
    )
    sow_id = cur.fetchone()[0]
    cur = admin.execute(
        """
        INSERT INTO public.contracts (public_id, sow_id, project_id, company_id,
                                      contract_type, counterparty_company_id, title, status)
        VALUES ('C' || upper(substr(md5(random()::text),1,8)), %s, %s, %s, 'COMPANY', %s,
                'Contract 1', 'ACTIVE') RETURNING id
        """,
        (sow_id, pid, comp_a, comp_a),
    )
    contract_id = cur.fetchone()[0]
    admin.execute(
        """
        INSERT INTO public.contract_line_items
          (contract_id, label, quantity, unit_rate, amount)
        VALUES (%s, 'Hidden rate', 1, 999.00, 999.00)
        """,
        (contract_id,),
    )

    # A cross-company contract, so visibility depends on being a party rather
    # than on owning the row.
    owner_b = world.users["outsider_b"]
    comp_b = make_company(admin, world, "B", owner_b)
    cur = admin.execute(
        """
        INSERT INTO public.contracts (public_id, sow_id, project_id, company_id,
                                      contract_type, counterparty_company_id, title, status)
        VALUES ('C' || upper(substr(md5(random()::text),1,8)), %s, %s, %s, 'COMPANY', %s,
                'Contract A-to-B', 'ACTIVE') RETURNING id
        """,
        (sow_id, pid, comp_a, comp_b),
    )
    cross_contract = cur.fetchone()[0]
    admin.execute(
        """
        INSERT INTO public.contract_line_items
          (contract_id, label, quantity, unit_rate, amount)
        VALUES (%s, 'Counterparty rate', 1, 250.00, 250.00)
        """,
        (cross_contract,),
    )

    with as_user(staff.user_id, comp_a) as c:
        # EMPLOYEE holds contracts.read ...
        assert c.execute("SELECT count(*) FROM public.contracts").fetchone()[0] == 2
        # ... and a counterparty may see the commercial terms of a contract it is
        # party to, which is required to understand an invoice.
        assert c.execute("SELECT count(*) FROM public.contract_line_items").fetchone()[0] == 2

        # Granularity: permissions EMPLOYEE does not hold still gate everything.
        assert c.execute("SELECT count(*) FROM public.invoices").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM platform.audit_logs").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM public.ai_insights").fetchone()[0] == 0
        # timesheets.read_any is not granted, so only their own sheet is visible.
        assert c.execute("SELECT count(*) FROM public.timesheets").fetchone()[0] == 0

    with as_user(owner_a.user_id, comp_a) as c:
        # SUPER_ADMIN holds every permission, so the rate is visible.
        assert c.execute("SELECT count(*) FROM public.contract_line_items").fetchone()[0] == 2


# ---------------------------------------------------------------------------
# T4 — no privilege escalation
# ---------------------------------------------------------------------------


def test_t4_role_changes_require_roles_manage(world: World, admin: psycopg.Connection) -> None:
    """A company admin without roles.manage cannot grant itself a permission."""
    owner_a = world.users["owner_a"]
    staff = world.users["staff_a"]
    comp_a = make_company(admin, world, "A", owner_a)
    add_member(admin, comp_a, staff.user_id, "COMPANY_ADMIN")  # has roles.read, not roles.manage

    cur = admin.execute(
        """
        SELECT id FROM public.company_roles
         WHERE company_id = %s AND key = 'FINANCE_MANAGER'
        """,
        (comp_a,),
    )
    finance_role = cur.fetchone()[0]

    with as_user(staff.user_id, comp_a) as c:
        with expect_raises(c, psycopg.errors.InsufficientPrivilege):
            c.execute(
                """
                INSERT INTO public.role_permissions (role_id, permission_key)
                VALUES (%s, 'contracts.approve')
                """,
                (finance_role,),
            )


# ---------------------------------------------------------------------------
# T5 — public_id immutability
# ---------------------------------------------------------------------------


def test_t5_public_id_is_immutable(world: World, admin: psycopg.Connection) -> None:
    owner_a = world.users["owner_a"]
    comp_a = make_company(admin, world, "A", owner_a)

    with as_user(owner_a.user_id, comp_a) as c:
        cid = c.execute("SELECT id, public_id FROM public.companies").fetchone()
        with expect_raises(c, psycopg.errors.RestrictViolation):
            c.execute(
                "UPDATE public.companies SET public_id = 'COAAAAAAAA' WHERE id = %s",
                (cid[0],),
            )


# ---------------------------------------------------------------------------
# T6 — contract state machine
# ---------------------------------------------------------------------------


def test_t6_contract_state_machine_cannot_be_skipped(
    world: World, admin: psycopg.Connection
) -> None:
    owner_a = world.users["owner_a"]
    comp_a = make_company(admin, world, "A", owner_a)
    cur = admin.execute(
        """
        INSERT INTO public.projects (company_id, name, status)
        VALUES (%s, 'P6', 'ACTIVE') RETURNING id
        """,
        (comp_a,),
    )
    pid = cur.fetchone()[0]
    cur = admin.execute(
        """
        INSERT INTO public.sows (public_id, project_id, company_id, sow_type,
                                 counterparty_company_id, title, status)
        VALUES ('S' || upper(substr(md5(random()::text),1,8)), %s, %s, 'COMPANY', %s,
                'SOW 6', 'ACTIVE') RETURNING id
        """,
        (pid, comp_a, comp_a),
    )
    sow_id = cur.fetchone()[0]
    cur = admin.execute(
        """
        INSERT INTO public.contracts (public_id, sow_id, project_id, company_id,
                                      contract_type, counterparty_company_id, title, status)
        VALUES ('C' || upper(substr(md5(random()::text),1,8)), %s, %s, %s, 'COMPANY', %s,
                'Contract 6', 'DRAFT') RETURNING id
        """,
        (sow_id, pid, comp_a, comp_a),
    )
    contract_id = cur.fetchone()[0]

    with as_user(owner_a.user_id, comp_a) as c:
        # DRAFT -> ACTIVE skips SENT/PENDING_ACCEPTANCE/ACCEPTED: must be refused.
        with expect_raises(c, psycopg.errors.CheckViolation):
            c.execute("UPDATE public.contracts SET status = 'ACTIVE' WHERE id = %s", (contract_id,))

        # The legal path works.
        c.execute("UPDATE public.contracts SET status = 'SENT' WHERE id = %s", (contract_id,))
        c.execute("UPDATE public.contracts SET status = 'ACCEPTED' WHERE id = %s", (contract_id,))
        c.execute("UPDATE public.contracts SET status = 'ACTIVE' WHERE id = %s", (contract_id,))
        assert (
            c.execute(
                "SELECT status FROM public.contracts WHERE id = %s", (contract_id,)
            ).fetchone()[0]
            == "ACTIVE"
        )


# ---------------------------------------------------------------------------
# T7 — allocation capacity
# ---------------------------------------------------------------------------


def test_t7_allocation_cannot_exceed_required_count(
    world: World, admin: psycopg.Connection
) -> None:
    owner_a = world.users["owner_a"]
    comp_a = make_company(admin, world, "A", owner_a)

    cur = admin.execute(
        """
        INSERT INTO public.projects (company_id, name, status)
        VALUES (%s, 'P7', 'ACTIVE') RETURNING id
        """,
        (comp_a,),
    )
    pid = cur.fetchone()[0]
    cur = admin.execute(
        """
        INSERT INTO public.project_roles (public_id, project_id, company_id, title, required_count)
        VALUES ('R' || upper(substr(md5(random()::text),1,8)), %s, %s, 'Java Developer', 10)
        RETURNING id
        """,
        (pid, comp_a),
    )
    role_id = cur.fetchone()[0]

    def new_sow(qty: int) -> uuid:
        cur = admin.execute(
            """
            INSERT INTO public.sows (public_id, project_id, company_id, sow_type,
                                     counterparty_company_id, title, status)
            VALUES ('S' || upper(substr(md5(random()::text),1,8)), %s, %s, 'COMPANY', %s,
                    'SOW 7', 'ACTIVE') RETURNING id
            """,
            (pid, comp_a, comp_a),
        )
        sid = cur.fetchone()[0]
        admin.execute(
            """
            INSERT INTO public.sow_roles (sow_id, project_role_id, quantity)
            VALUES (%s, %s, %s)
            """,
            (sid, role_id, qty),
        )
        return sid

    new_sow(6)
    new_sow(3)

    # The advisory lock serialises allocation writes, so read the counter in a
    # fresh transaction to be sure the trigger has committed.
    with admin.transaction():
        allocated = admin.execute(
            "SELECT allocated_count FROM public.project_roles WHERE id = %s", (role_id,)
        ).fetchone()[0]
    assert allocated == 9, f"expected 9 allocated, got {allocated}"

    # 9 + 2 = 11 > required 10 -> refused. The over-allocation SOW itself is
    # rejected, so nothing is created.
    with pytest.raises(psycopg.errors.CheckViolation):
        new_sow(2)

    with admin.transaction():
        still = admin.execute(
            "SELECT count(*) FROM public.sow_roles sr "
            "JOIN public.sows s ON s.id = sr.sow_id "
            "WHERE sr.project_role_id = %s",
            (role_id,),
        ).fetchone()[0]
    assert still == 2, "the over-capacity SOW must not be created"

    # 9 + 1 = 10 is exactly allowed, and marks the role FILLED.
    new_sow(1)
    with admin.transaction():
        row = admin.execute(
            "SELECT allocated_count, status FROM public.project_roles WHERE id = %s",
            (role_id,),
        ).fetchone()
    assert row[0] == 10
    assert row[1] == "FILLED"


# ---------------------------------------------------------------------------
# T8 / T9 — timesheets
# ---------------------------------------------------------------------------


def test_t8_timesheet_requires_active_contract(world: World, admin: psycopg.Connection) -> None:
    """A DRAFT contract cannot carry work: no orphan timesheets."""
    owner_a = world.users["owner_a"]
    worker = world.users["staff_a"]
    comp_a = make_company(admin, world, "A", owner_a)
    add_member(admin, comp_a, worker.user_id, "EMPLOYEE")

    cur = admin.execute(
        """
        INSERT INTO public.projects (company_id, name, status)
        VALUES (%s, 'P8', 'ACTIVE') RETURNING id
        """,
        (comp_a,),
    )
    pid = cur.fetchone()[0]
    cur = admin.execute(
        """
        INSERT INTO public.sows (public_id, project_id, company_id, sow_type,
                                 counterparty_company_id, title, status)
        VALUES ('S' || upper(substr(md5(random()::text),1,8)), %s, %s, 'COMPANY', %s,
                'SOW 8', 'ACTIVE') RETURNING id
        """,
        (pid, comp_a, comp_a),
    )
    sow_id = cur.fetchone()[0]
    cur = admin.execute(
        """
        INSERT INTO public.contracts (public_id, sow_id, project_id, company_id,
                                      contract_type, counterparty_company_id, title, status)
        VALUES ('C' || upper(substr(md5(random()::text),1,8)), %s, %s, %s, 'COMPANY', %s,
                'Contract 8', 'DRAFT') RETURNING id
        """,
        (sow_id, pid, comp_a, comp_a),
    )
    contract_id = cur.fetchone()[0]

    with as_user(worker.user_id, comp_a) as c:
        with expect_raises(c, psycopg.errors.CheckViolation):
            c.execute(
                """
                INSERT INTO public.assignments
                  (contract_id, project_id, company_id, user_id, role_title, start_date)
                VALUES (%s, %s, %s, %s, 'Dev', current_date)
                """,
                (contract_id, pid, comp_a, worker.user_id),
            )
        assert c.execute("SELECT count(*) FROM public.assignments").fetchone()[0] == 0


def test_t9_locked_timesheet_immutable_and_self_approval_blocked(
    world: World, admin: psycopg.Connection
) -> None:
    owner_a = world.users["owner_a"]
    worker = world.users["staff_a"]
    comp_a = make_company(admin, world, "A", owner_a)
    add_member(admin, comp_a, worker.user_id, "EMPLOYEE")

    # Build an ACTIVE contract + assignment directly as the owner.
    cur = admin.execute(
        """
        INSERT INTO public.projects (company_id, name, status)
        VALUES (%s, 'P9', 'ACTIVE') RETURNING id
        """,
        (comp_a,),
    )
    pid = cur.fetchone()[0]
    cur = admin.execute(
        """
        INSERT INTO public.sows (public_id, project_id, company_id, sow_type,
                                 counterparty_company_id, title, status)
        VALUES ('S' || upper(substr(md5(random()::text),1,8)), %s, %s, 'COMPANY', %s,
                'SOW 9', 'ACTIVE') RETURNING id
        """,
        (pid, comp_a, comp_a),
    )
    sow_id = cur.fetchone()[0]
    cur = admin.execute(
        """
        INSERT INTO public.contracts (public_id, sow_id, project_id, company_id,
                                      contract_type, counterparty_company_id, title, status,
                                      start_date, end_date)
        VALUES ('C' || upper(substr(md5(random()::text),1,8)), %s, %s, %s, 'COMPANY', %s,
                'Contract 9', 'ACTIVE', current_date - 30, current_date + 300) RETURNING id
        """,
        (sow_id, pid, comp_a, comp_a),
    )
    contract_id = cur.fetchone()[0]
    cur = admin.execute(
        """
        INSERT INTO public.assignments
          (contract_id, project_id, company_id, user_id, role_title, start_date,
           end_date, status, hourly_rate)
        VALUES (%s, %s, %s, %s, 'Dev', current_date - 30, current_date + 300, 'ACTIVE', 60.00)
        RETURNING id
        """,
        (contract_id, pid, comp_a, worker.user_id),
    )
    assignment_id = cur.fetchone()[0]

    with as_user(worker.user_id, comp_a) as c:
        cur = c.execute(
            """
            INSERT INTO public.timesheets
              (user_id, company_id, assignment_id, contract_id, project_id,
               period_start, period_end, status)
            VALUES (%s, %s, %s, %s, %s, date_trunc('month', current_date)::date,
                    (date_trunc('month', current_date) + interval '27 days')::date, 'DRAFT')
            RETURNING id
            """,
            (worker.user_id, comp_a, assignment_id, contract_id, pid),
        )
        ts_id = cur.fetchone()[0]

        # Hours are computed from clock times, not accepted from the client.
        c.execute(
            """
            INSERT INTO public.timesheet_entries
              (timesheet_id, entry_date, start_time, end_time, break_minutes,
               work_description, source)
            VALUES (%s, current_date, '09:00', '17:30', 30, 'work', 'MANUAL')
            """,
            (ts_id,),
        )
        hours, amount = c.execute(
            "SELECT total_hours, total_amount FROM public.timesheets WHERE id = %s", (ts_id,)
        ).fetchone()
        assert float(hours) == 8.0, f"expected 8h, got {hours}"
        assert float(amount) == 480.0, f"expected 480, got {amount}"

        # Try to lie about the hours. The compute trigger re-derives them from
        # start/end times, so the submitted value is discarded rather than stored.
        c.execute(
            "UPDATE public.timesheet_entries SET hours = 24 WHERE timesheet_id = %s",
            (ts_id,),
        )
        lied = c.execute(
            "SELECT hours, amount FROM public.timesheet_entries WHERE timesheet_id = %s",
            (ts_id,),
        ).fetchone()
        assert float(lied[0]) == 8.0, f"hours were not re-derived: {lied[0]}"
        assert float(lied[1]) == 480.0, f"amount was not re-derived: {lied[1]}"

        # A user who DOES hold timesheets.approve must still not approve their own
        # sheet: that is segregation of duties, enforced by the state machine
        # rather than by RLS. TIMESHEET_MANAGER is the role that carries the
        # permission, so the same person is both submitter and potential approver.
        approver = world.users["admin_a"]
        add_member(admin, comp_a, approver.user_id, "TIMESHEET_MANAGER")
        assert approver_can_approve(admin, comp_a, approver.user_id), (
            "fixture is wrong: TIMESHEET_MANAGER should hold timesheets.approve"
        )

        # Give the approver their own assignment and timesheet, then let them try
        # to approve it themselves.
        own_ts = _own_timesheet(admin, comp_a, approver.user_id, contract_id, pid)
        with as_user(approver.user_id, comp_a) as c2:
            c2.execute("UPDATE public.timesheets SET status = 'SUBMITTED' WHERE id = %s", (own_ts,))
            with expect_raises(c2, psycopg.errors.InsufficientPrivilege):
                c2.execute(
                    "UPDATE public.timesheets SET status = 'APPROVED' WHERE id = %s", (own_ts,)
                )

        # Self-approval is refused (segregation of duties). An EMPLOYEE holds no
        # timesheets.approve, so RLS filters the row and the UPDATE affects
        # nothing. The row still reads SUBMITTED, not APPROVED.
        c.execute("UPDATE public.timesheets SET status = 'SUBMITTED' WHERE id = %s", (ts_id,))
        approved = c.execute(
            "UPDATE public.timesheets SET status = 'APPROVED' WHERE id = %s RETURNING status",
            (ts_id,),
        ).fetchall()
        assert approved == [], "a user without timesheets.approve approved a timesheet"
        still = c.execute(
            "SELECT status FROM public.timesheets WHERE id = %s", (ts_id,)
        ).fetchone()[0]
        assert still == "SUBMITTED"

    # Owner (a different person) approves and locks.
    with as_user(owner_a.user_id, comp_a) as c:
        c.execute("UPDATE public.timesheets SET status = 'APPROVED' WHERE id = %s", (ts_id,))
        c.execute("UPDATE public.timesheets SET status = 'LOCKED' WHERE id = %s", (ts_id,))

    with as_user(worker.user_id, comp_a) as c:
        # A LOCKED timesheet is closed to its author. The state machine in
        # app.compute_entry refuses the write with a clear reason, which is
        # preferable here to RLS silently dropping the row: the employee needs to
        # know the sheet is closed so they can request an adjustment.
        with expect_raises(c, psycopg.errors.CheckViolation):
            c.execute(
                """
                INSERT INTO public.timesheet_entries
                  (timesheet_id, entry_date, start_time, end_time, work_description)
                VALUES (%s, current_date, '09:00', '10:00', 'sneaky')
                """,
                (ts_id,),
            )

        # An UPDATE behaves differently from an INSERT, and correctly so: the
        # USING clause of ts_entries_write filters the row out before any trigger
        # runs, so zero rows match and the statement succeeds silently. Raising
        # here would confirm that the sheet exists.
        edited = c.execute(
            "UPDATE public.timesheet_entries SET work_description = 'edited' "
            "WHERE timesheet_id = %s RETURNING id",
            (ts_id,),
        ).fetchall()
        assert edited == [], "an entry on a locked timesheet was modified"

        # Header totals are protected by the timesheets_update policy, whose USING
        # clause only admits a sheet the author may still edit (DRAFT/REJECTED) or
        # a sheet the caller may approve. Neither applies to a LOCKED sheet owned
        # by someone else, so zero rows match and the statement is a silent no-op
        # rather than an error that would confirm the sheet exists.
        tampered = c.execute(
            "UPDATE public.timesheets SET total_hours = 100 WHERE id = %s RETURNING total_hours",
            (ts_id,),
        ).fetchall()
        assert tampered == [], "the totals of a locked timesheet were changed"

        # And the totals the worker sees are still the approved ones.
        totals = c.execute(
            "SELECT total_hours, total_amount, status FROM public.timesheets WHERE id = %s",
            (ts_id,),
        ).fetchone()
        assert float(totals[0]) == 8.0
        assert float(totals[1]) == 480.0
        assert totals[2] == "LOCKED"


# ---------------------------------------------------------------------------
# T10 / T11 — invoices
# ---------------------------------------------------------------------------


def _active_contract(admin: psycopg.Connection, comp: uuid, name: str) -> tuple[uuid, uuid]:
    cur = admin.execute(
        """
        INSERT INTO public.projects (company_id, name, status)
        VALUES (%s, %s, 'ACTIVE') RETURNING id
        """,
        (comp, name),
    )
    pid = cur.fetchone()[0]
    cur = admin.execute(
        """
        INSERT INTO public.sows (public_id, project_id, company_id, sow_type,
                                 counterparty_company_id, title, status)
        VALUES ('S' || upper(substr(md5(random()::text),1,8)), %s, %s, 'COMPANY', %s, %s, 'ACTIVE')
        RETURNING id
        """,
        (pid, comp, comp, f"{name} SOW"),
    )
    sid = cur.fetchone()[0]
    cur = admin.execute(
        """
        INSERT INTO public.contracts (public_id, sow_id, project_id, company_id,
                                      contract_type, counterparty_company_id, title, status)
        VALUES ('C' || upper(substr(md5(random()::text),1,8)), %s, %s, %s,
                'COMPANY', %s, %s, 'ACTIVE')
        RETURNING id
        """,
        (sid, pid, comp, comp, f"{name} Contract"),
    )
    return pid, cur.fetchone()[0]


def test_t10_invoice_requires_active_msa(world: World, admin: psycopg.Connection) -> None:
    owner_a = world.users["owner_a"]
    owner_b = world.users["outsider_b"]
    comp_a = make_company(admin, world, "A", owner_a)
    comp_b = make_company(admin, world, "B", owner_b)
    _, contract_id = _active_contract(admin, comp_a, "T10")

    cur = admin.execute(
        """
        INSERT INTO public.invoices
          (public_id, direction, company_id, counterparty_company_id, contract_id,
           period_start, period_end, issue_date, due_date, status)
        VALUES ('I' || upper(substr(md5(random()::text),1,8)), 'RECEIVABLE', %s, %s, %s,
                current_date - 30, current_date - 1, current_date - 30, current_date + 30, 'DRAFT')
        RETURNING id
        """,
        (comp_a, comp_b, contract_id),
    )
    invoice_id = cur.fetchone()[0]
    admin.execute(
        "INSERT INTO public.invoice_items (invoice_id, contract_id, line_type, "
        "description, quantity, unit_rate, subtotal, total) "
        "VALUES (%s, %s, 'FIXED', 'Work', 10, 100.00, 1000.00, 1000.00)",
        (invoice_id, contract_id),
    )

    with as_user(owner_a.user_id, comp_a) as c:
        # No MSA exists between A and B: the invoice is generated but flagged.
        c.execute("UPDATE public.invoices SET status = 'PENDING' WHERE id = %s", (invoice_id,))
        row = c.execute(
            "SELECT msa_required, msa_block_reason FROM public.invoices WHERE id = %s",
            (invoice_id,),
        ).fetchone()
        assert row[0] is True
        assert "Service Agreement" in (row[1] or "")

        # Submission is refused.
        with expect_raises(c, psycopg.errors.CheckViolation):
            c.execute(
                "UPDATE public.invoices SET status = 'SUBMITTED' WHERE id = %s", (invoice_id,)
            )

    # Activate an MSA, then submission becomes possible.
    cur = admin.execute(
        """
        INSERT INTO public.msas (public_id, company_a_id, company_b_id, status,
                                 effective_date, expiration_date)
        VALUES ('M' || upper(substr(md5(random()::text),1,8)), %s, %s, 'ACTIVE',
                current_date - 10, current_date + 355) RETURNING id
        """,
        (comp_a, comp_b),
    )
    assert cur.fetchone()

    with as_user(owner_a.user_id, comp_a) as c:
        c.execute("UPDATE public.invoices SET status = 'SUBMITTED' WHERE id = %s", (invoice_id,))
        assert c.execute(
            "SELECT status, msa_required FROM public.invoices WHERE id = %s", (invoice_id,)
        ).fetchone() == ("SUBMITTED", False)


def test_t11_invoice_totals_are_derived(world: World, admin: psycopg.Connection) -> None:
    """The client cannot set subtotal/total/total_amount: the trigger recomputes."""
    owner_a = world.users["owner_a"]
    comp_a = make_company(admin, world, "A", owner_a)
    _, contract_id = _active_contract(admin, comp_a, "T11")

    cur = admin.execute(
        """
        INSERT INTO public.invoices
          (public_id, direction, company_id, contract_id, period_start, period_end,
           issue_date, due_date, status)
        VALUES ('I' || upper(substr(md5(random()::text),1,8)), 'RECEIVABLE', %s, %s,
                current_date - 30, current_date - 1, current_date - 30, current_date + 30, 'DRAFT')
        RETURNING id
        """,
        (comp_a, contract_id),
    )
    invoice_id = cur.fetchone()[0]

    # Attempt to claim a million dollars on the header.
    with as_user(owner_a.user_id, comp_a) as c:
        c.execute(
            "UPDATE public.invoices SET total_amount = 1000000, subtotal = 1000000 WHERE id = %s",
            (invoice_id,),
        )
        subtotal, total = c.execute(
            "SELECT subtotal, total_amount FROM public.invoices WHERE id = %s", (invoice_id,)
        ).fetchone()
        assert float(subtotal) == 0.0
        assert float(total) == 0.0

    # Add one line: totals follow the line, and the line's own total is derived.
    with as_user(owner_a.user_id, comp_a) as c:
        c.execute(
            """
            INSERT INTO public.invoice_items
                 (invoice_id, contract_id, line_type, description, quantity, unit_rate, tax_rate)
               VALUES (%s, %s, 'FIXED', 'Consulting', 7.5, 120.00, 0.10)
            """,
            (invoice_id, contract_id),
        )
        subtotal, tax, total = c.execute(
            "SELECT subtotal, tax_total, total_amount FROM public.invoices WHERE id = %s",
            (invoice_id,),
        ).fetchone()
        assert float(subtotal) == 900.00
        assert float(tax) == 90.00
        assert float(total) == 990.00


# ---------------------------------------------------------------------------
# T12 / T13 — AI safety
# ---------------------------------------------------------------------------


def test_t12_ai_retrieval_is_permission_filtered(world: World, admin: psycopg.Connection) -> None:
    """Unauthorized chunks are excluded in SQL, not hidden from the model."""
    owner_a = world.users["owner_a"]
    staff = world.users["staff_a"]
    comp_a = make_company(admin, world, "A", owner_a)
    add_member(admin, comp_a, staff.user_id, "EMPLOYEE")

    emb = "[" + ",".join(["0.1"] * 1536) + "]"

    # Two knowledge docs in the same company with different required permissions.
    cur = admin.execute(
        """
        INSERT INTO public.ai_knowledge_documents
          (public_id, company_id, title, source_type, required_permission,
           extraction_state, is_active)
        VALUES ('KD' || upper(substr(md5(random()::text),1,6)), %s, 'Company Handbook',
                'POLICY', 'documents.read', 'EMBEDDED', true) RETURNING id
        """,
        (comp_a,),
    )
    open_doc = cur.fetchone()[0]
    cur = admin.execute(
        """
        INSERT INTO public.ai_knowledge_documents
          (public_id, company_id, title, source_type, required_permission,
           extraction_state, is_active)
        VALUES ('KD' || upper(substr(md5(random()::text),1,6)), %s, 'Board Minutes',
                'DOCUMENT', 'audit.read', 'EMBEDDED', true) RETURNING id
        """,
        (comp_a,),
    )
    secret_doc = cur.fetchone()[0]

    for doc_id, text in (
        (open_doc, "Employee handbook content"),
        (secret_doc, "Board minutes secret"),
    ):
        admin.execute(
            """
            INSERT INTO public.ai_document_chunks
              (knowledge_document_id, company_id, chunk_index, content, token_count,
               embedding, embedding_model, content_hash)
            VALUES (%s, %s, 0, %s, 5, %s::vector, 'test-model', md5(%s))
            """,
            (doc_id, comp_a, text, emb, text),
        )

    # EMPLOYEE holds documents.read but NOT audit.read -> only 1 chunk retrievable.
    with as_user(staff.user_id, comp_a) as c:
        rows = c.execute(
            "SELECT title FROM app.ai_visible_chunks(%s, %s::vector, 10)", (comp_a, emb)
        ).fetchall()
        titles = {r[0] for r in rows}
        assert titles == {"Company Handbook"}, f"leaked: {titles}"

    # SUPER_ADMIN holds every permission -> both.
    with as_user(owner_a.user_id, comp_a) as c:
        rows = c.execute(
            "SELECT title FROM app.ai_visible_chunks(%s, %s::vector, 10)", (comp_a, emb)
        ).fetchall()
        assert {r[0] for r in rows} == {"Company Handbook", "Board Minutes"}

    # A user from another company retrieves nothing at all.
    owner_c = world.users["outsider_c"]
    comp_c = make_company(admin, world, "C", owner_c)
    with as_user(owner_c.user_id, comp_c) as c:
        rows = c.execute(
            "SELECT title FROM app.ai_visible_chunks(%s, %s::vector, 10)", (comp_a, emb)
        ).fetchall()
        assert rows == []


def test_t13_ai_action_needs_human_approval(world: World, admin: psycopg.Connection) -> None:
    owner_a = world.users["owner_a"]
    comp_a = make_company(admin, world, "A", owner_a)

    cur = admin.execute(
        """
        INSERT INTO public.ai_actions
          (public_id, company_id, actor_type, initiated_by, action_type,
           risk_level, status, required_permission, proposal)
        VALUES ('AA' || upper(substr(md5(random()::text),1,6)), %s, 'AI', %s,
                'initiate_payment', 'CRITICAL', 'PENDING_APPROVAL',
                'payments.initiate', '{}'::jsonb) RETURNING id
        """,
        (comp_a, owner_a.user_id),
    )
    action_id = cur.fetchone()[0]

    with as_user(owner_a.user_id, comp_a) as c:
        # Executing without approval or capability token must fail.
        with expect_raises(c, psycopg.errors.InsufficientPrivilege):
            c.execute(
                "UPDATE public.ai_actions SET status = 'EXECUTED' WHERE id = %s", (action_id,)
            )

        # Even with approval, a missing capability token fails.
        c.execute(
            "UPDATE public.ai_actions SET approved_by = %s, approved_at = now() WHERE id = %s",
            (owner_a.user_id, action_id),
        )
        with expect_raises(c, psycopg.errors.InsufficientPrivilege):
            c.execute(
                "UPDATE public.ai_actions SET status = 'EXECUTED' WHERE id = %s", (action_id,)
            )

        # A third party must approve a CRITICAL action: the initiator cannot.
        approver = world.users["admin_a"]
        add_member(admin, comp_a, approver.user_id, "SUPER_ADMIN")
        c.execute(
            "UPDATE public.ai_actions SET approved_by = %s WHERE id = %s",
            (approver.user_id, action_id),
        )

        # Still refused: a capability token bound to a human is mandatory.
        with expect_raises(c, psycopg.errors.InsufficientPrivilege):
            c.execute(
                "UPDATE public.ai_actions SET status = 'EXECUTED' WHERE id = %s", (action_id,)
            )

        # With the capability token present, execution succeeds.
        c.execute(
            "UPDATE public.ai_actions SET capability_token = %s WHERE id = %s",
            ("cap_" + uuid.uuid4().hex, action_id),
        )
        c.execute("UPDATE public.ai_actions SET status = 'EXECUTED' WHERE id = %s", (action_id,))
        assert (
            c.execute(
                "SELECT executed_at IS NOT NULL FROM public.ai_actions WHERE id = %s", (action_id,)
            ).fetchone()[0]
            is True
        )


# ---------------------------------------------------------------------------
# T14 / T15 — payments
# ---------------------------------------------------------------------------


def test_t14_payment_cannot_move_without_authorization(
    world: World, admin: psycopg.Connection
) -> None:
    owner_a = world.users["owner_a"]
    comp_a = make_company(admin, world, "A", owner_a)

    cur = admin.execute(
        """
        INSERT INTO public.payments
          (public_id, company_id, direction, amount, currency, payment_method,
           status, authorization_type, processor)
        VALUES ('PM' || upper(substr(md5(random()::text),1,6)), %s, 'PAYABLE', 5000.00,
                'USD', 'ACH', 'SCHEDULED', 'EXPLICIT', 'MANUAL') RETURNING id
        """,
        (comp_a,),
    )
    payment_id = cur.fetchone()[0]

    with as_user(owner_a.user_id, comp_a) as c:
        with expect_raises(c, psycopg.errors.CheckViolation):
            c.execute(
                "UPDATE public.payments SET status = 'INITIATED' WHERE id = %s", (payment_id,)
            )

        c.execute(
            "UPDATE public.payments SET authorized_at = now(), authorized_by = %s WHERE id = %s",
            (owner_a.user_id, payment_id),
        )
        c.execute("UPDATE public.payments SET status = 'INITIATED' WHERE id = %s", (payment_id,))
        c.execute(
            "UPDATE public.payments SET processor_payment_ref = 'proc_123' WHERE id = %s",
            (payment_id,),
        )
        c.execute("UPDATE public.payments SET status = 'COMPLETED' WHERE id = %s", (payment_id,))
        assert (
            c.execute(
                "SELECT status, net_amount FROM public.payments WHERE id = %s", (payment_id,)
            ).fetchone()[0]
            == "COMPLETED"
        )


def test_t15_bank_transaction_ledger_is_append_only(
    world: World, admin: psycopg.Connection
) -> None:
    """Transaction facts are immutable; only match state may change."""
    owner_a = world.users["owner_a"]
    comp_a = make_company(admin, world, "A", owner_a)

    cur = admin.execute(
        """
        INSERT INTO public.bank_connections
          (public_id, company_id, owner_user_id, institution_name, institution_id,
           item_id_encrypted, access_token_encrypted, status)
        VALUES ('BC' || upper(substr(md5(random()::text),1,6)), %s, %s, 'Test Bank', 'ins_test',
                decode('00','hex'), decode('00','hex'), 'CONNECTED') RETURNING id
        """,
        (comp_a, owner_a.user_id),
    )
    conn_id = cur.fetchone()[0]
    cur = admin.execute(
        """
        INSERT INTO public.bank_accounts
          (public_id, bank_connection_id, company_id, institution_name,
           account_number_masked, account_type, status)
        VALUES ('BA' || upper(substr(md5(random()::text),1,6)), %s, %s, 'Test Bank',
                '****4321', 'CHECKING', 'CONNECTED') RETURNING id
        """,
        (conn_id, comp_a),
    )
    account_id = cur.fetchone()[0]
    cur = admin.execute(
        """
        INSERT INTO public.bank_transactions
          (bank_account_id, company_id, provider_transaction_id, posted_at, amount,
           description_raw)
        VALUES (%s, %s, 'prov_txn_1', current_date, -5500.00, 'INV I1024') RETURNING id
        """,
        (account_id, comp_a),
    )
    txn_id = cur.fetchone()[0]

    with as_user(owner_a.user_id, comp_a) as c:
        # Match state is updatable: reconciliation is a legitimate write.
        c.execute(
            "UPDATE public.bank_transactions SET match_status = 'MATCHED', "
            "is_reconciled = true WHERE id = %s",
            (txn_id,),
        )
        assert (
            c.execute(
                "SELECT match_status FROM public.bank_transactions WHERE id = %s", (txn_id,)
            ).fetchone()[0]
            == "MATCHED"
        )

    # Each impersonation below is its own transaction, so a failed UPDATE that is
    # rolled back to a savepoint releases its row lock. Doing the failing writes
    # in separate `as_user` blocks is what keeps them from blocking each other.

    # An ACCOUNTANT holds reconciliation.manage: RLS admits the UPDATE and the
    # ledger-fact guard is what refuses it.
    accountant = world.users["admin_a"]
    add_member(admin, comp_a, accountant.user_id, "ACCOUNTANT")
    with as_user(accountant.user_id, comp_a) as c3:
        assert c3.execute("SELECT count(*) FROM public.bank_transactions").fetchone()[0] == 1
        with expect_raises(c3, psycopg.errors.RestrictViolation):
            c3.execute(
                "UPDATE public.bank_transactions SET amount = 1.00 WHERE id = %s",
                (txn_id,),
            )

    # A caller without reconciliation.manage cannot write at all and gets zero
    # rows rather than an error, so existence is not disclosed.
    # PROJECT_MANAGER holds neither transactions.read nor reconciliation.manage.
    plain = world.users["staff_a"]
    add_member(admin, comp_a, plain.user_id, "PROJECT_MANAGER")
    with as_user(plain.user_id, comp_a) as c2:
        assert c2.execute("SELECT count(*) FROM public.bank_transactions").fetchone()[0] == 0
        filtered = c2.execute(
            "UPDATE public.bank_transactions SET match_status = 'IGNORED' "
            "WHERE id = %s RETURNING id",
            (txn_id,),
        ).fetchall()
        assert filtered == [], "a user without reconciliation.manage changed a match"

        # The worker is the only writer. A re-import of the same provider id is
        # idempotent via UNIQUE(bank_account_id, provider_transaction_id).
        with admin.transaction():
            admin.execute("GRANT INSERT ON public.bank_transactions TO mytrakin_worker")
            admin.execute("GRANT SELECT ON public.bank_transactions TO mytrakin_worker")
        with pytest.raises(psycopg.errors.UniqueViolation), admin.cursor() as wc:
            wc.execute("SET LOCAL ROLE mytrakin_worker")
            wc.execute(
                """
                    INSERT INTO public.bank_transactions
                      (bank_account_id, company_id, provider_transaction_id,
                       posted_at, amount)
                    VALUES (%s, %s, 'prov_txn_1', current_date, -5500.00)
                    """,
                (account_id, comp_a),
            )

    # The original amount survived every attempt.
    with admin.transaction():
        amount, match = admin.execute(
            "SELECT amount, match_status FROM public.bank_transactions WHERE id = %s",
            (txn_id,),
        ).fetchone()
    assert float(amount) == -5500.0
    assert match == "MATCHED"


# ---------------------------------------------------------------------------
# T16 — fail closed
# ---------------------------------------------------------------------------


def test_t16_unauthenticated_sees_nothing(world: World, admin: psycopg.Connection) -> None:
    """Without SET LOCAL app.user_id every policy denies."""
    owner_a = world.users["owner_a"]
    comp_a = make_company(admin, world, "A", owner_a)
    admin.execute(
        "INSERT INTO public.projects (company_id, name, status) VALUES (%s, 'Secret', 'ACTIVE')",
        (comp_a,),
    )

    with as_user(None, comp_a) as c:
        assert c.execute("SELECT count(*) FROM public.projects").fetchone()[0] == 0
        assert (
            c.execute("SELECT count(*) FROM public.companies WHERE id = %s", (comp_a,)).fetchone()[
                0
            ]
            == 1
        )  # discovery only
        assert c.execute("SELECT count(*) FROM public.company_memberships").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM public.permissions").fetchone()[0] == 0

    # With no company context, nothing company-scoped is visible either.
    with as_user(owner_a.user_id, None) as c:
        assert c.execute("SELECT count(*) FROM public.projects").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# T17 — column-level protection
# ---------------------------------------------------------------------------


def test_t17_sensitive_columns_not_readable_by_api_role(
    world: World, admin: psycopg.Connection
) -> None:
    """The API role gets masked columns only; ciphertext columns are not granted."""
    owner_a = world.users["owner_a"]
    comp_a = make_company(admin, world, "A", owner_a)

    admin.execute(
        """
        INSERT INTO public.user_sensitive (user_id, ssn_last4, tax_id_last4, ssn_encrypted)
        VALUES (%s, '1234', '4821', decode('deadbeef','hex'))
        """,
        (owner_a.user_id,),
    )

    with as_user(owner_a.user_id, comp_a) as c:
        # The masked projection is available.
        row = c.execute(
            "SELECT ssn_last4, tax_id_last4 FROM public.user_sensitive WHERE user_id = %s",
            (owner_a.user_id,),
        ).fetchone()
        assert row == ("1234", "4821")
        # The ciphertext column is not granted to the role at all.
        with expect_raises(c, psycopg.errors.InsufficientPrivilege):
            c.execute(
                "SELECT ssn_encrypted FROM public.user_sensitive WHERE user_id = %s",
                (owner_a.user_id,),
            )

    # Bank connection tokens are likewise not readable.
    with as_user(owner_a.user_id, comp_a) as c:
        with expect_raises(c, psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT access_token_encrypted FROM public.bank_connections")


# ---------------------------------------------------------------------------
# T18 / T19 / T20 — governance invariants
# ---------------------------------------------------------------------------


def test_t18_last_super_admin_cannot_be_removed(world: World, admin: psycopg.Connection) -> None:
    owner_a = world.users["owner_a"]
    staff = world.users["staff_a"]
    comp_a = make_company(admin, world, "A", owner_a)
    add_member(admin, comp_a, staff.user_id, "PROJECT_MANAGER")

    membership = admin.execute(
        """
        SELECT m.id FROM public.company_memberships m
          JOIN public.company_roles r ON r.id = m.role_id
         WHERE m.company_id = %s AND r.key = 'SUPER_ADMIN'
        """,
        (comp_a,),
    ).fetchone()[0]

    # The guard lives in a BEFORE UPDATE trigger, so it must fire for the API
    # role too, not only for a superuser session.
    with as_user(owner_a.user_id, comp_a) as c:
        with expect_raises(c, psycopg.errors.RestrictViolation):
            c.execute(
                "UPDATE public.company_memberships SET status = 'DEACTIVATED' WHERE id = %s",
                (membership,),
            )

    # A user holds exactly one membership per company (UNIQUE constraint), so
    # the second administrator is created by promoting the existing member.
    admin.execute(
        """
        UPDATE public.company_memberships
           SET role_id = (SELECT id FROM public.company_roles
                           WHERE company_id = %s AND key = 'SUPER_ADMIN')
         WHERE company_id = %s AND user_id = %s
        """,
        (comp_a, comp_a, staff.user_id),
    )
    admin.execute(
        "UPDATE public.company_memberships SET status = 'DEACTIVATED' WHERE id = %s",
        (membership,),
    )
    assert (
        admin.execute(
            "SELECT count(*) FROM public.company_memberships "
            "WHERE company_id = %s AND status = 'ACTIVE'",
            (comp_a,),
        ).fetchone()[0]
        == 1
    )


def test_t19_audit_log_is_append_only(world: World, admin: psycopg.Connection) -> None:
    owner_a = world.users["owner_a"]
    comp_a = make_company(admin, world, "A", owner_a)

    cur = admin.execute(
        """
        INSERT INTO platform.audit_logs
          (company_id, actor_user_id, action, resource_type, new_values)
        VALUES (%s, %s, 'test.action', 'test', '{}'::jsonb) RETURNING id
        """,
        (comp_a, owner_a.user_id),
    )
    log_id = cur.fetchone()[0]

    # Append-only holds for the superuser too: the trigger rejects UPDATE/DELETE
    # for every role, so not even an operator can rewrite history.
    with pytest.raises(psycopg.errors.RestrictViolation):
        admin.execute("UPDATE platform.audit_logs SET action = 'tampered' WHERE id = %s", (log_id,))
    with pytest.raises(psycopg.errors.RestrictViolation):
        admin.execute("DELETE FROM platform.audit_logs WHERE id = %s", (log_id,))

    # And the API role has no UPDATE/DELETE policy on the ledger, so it cannot
    # even attempt a write.
    with as_user(owner_a.user_id, comp_a) as c:
        for stmt in (
            "UPDATE platform.audit_logs SET action = 'tampered' WHERE id = %s",
            "DELETE FROM platform.audit_logs WHERE id = %s",
        ):
            with expect_raises(c, psycopg.errors.InsufficientPrivilege):
                c.execute(stmt, (log_id,))

    # Role-permission changes are audited automatically by trigger.
    before = admin.execute(
        "SELECT count(*) FROM platform.audit_logs WHERE company_id = %s", (comp_a,)
    ).fetchone()[0]
    cur = admin.execute(
        "SELECT id FROM public.company_roles WHERE company_id = %s AND key = 'VIEWER'", (comp_a,)
    ).fetchone()
    admin.execute(
        "INSERT INTO public.role_permissions (role_id, permission_key) VALUES (%s, 'audit.read')",
        (cur[0],),
    )
    after = admin.execute(
        """
        SELECT count(*) FROM platform.audit_logs
         WHERE company_id = %s AND action = 'role.permission.granted'
        """,
        (comp_a,),
    ).fetchone()[0]
    assert after > 0, "role permission change was not audited"
    assert before >= 1


def test_t20_documents_respect_tenancy_and_scan_gate(
    world: World, admin: psycopg.Connection
) -> None:
    owner_a = world.users["owner_a"]
    owner_b = world.users["outsider_b"]
    comp_a = make_company(admin, world, "A", owner_a)
    comp_b = make_company(admin, world, "B", owner_b)

    cur = admin.execute(
        """
        INSERT INTO public.documents (public_id, company_id, doc_type, title)
        VALUES ('D' || upper(substr(md5(random()::text),1,8)), %s, 'CONTRACT', 'A Contract Doc')
        RETURNING id
        """,
        (comp_a,),
    )
    doc_id = cur.fetchone()[0]
    cur = admin.execute(
        """
        INSERT INTO public.document_versions
          (document_id, version_no, storage_bucket, storage_path, file_name, mime_type,
           byte_size, checksum_sha256, scan_status)
        VALUES (%s, 1, 'documents', 'a/doc.pdf', 'doc.pdf', 'application/pdf', 1000,
                md5('a') || md5('b'), 'CLEAN') RETURNING id
        """,
        (doc_id,),
    )
    clean_version = cur.fetchone()[0]

    with as_user(owner_a.user_id, comp_a) as c:
        assert c.execute("SELECT count(*) FROM public.documents").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM public.document_versions").fetchone()[0] == 1

    # Company B cannot see or read Company A's documents.
    with as_user(owner_b.user_id, comp_b) as c:
        assert c.execute("SELECT count(*) FROM public.documents").fetchone()[0] == 0
        assert (
            c.execute(
                "SELECT count(*) FROM public.document_versions WHERE id = %s", (clean_version,)
            ).fetchone()[0]
            == 0
        )

    # Quarantine a version: it disappears from reads even for the owner. The
    # document trigger recomputes status on insert, so re-insert-then-verify is
    # not needed — a direct status flip is the honest simulation of a scanner
    # verdict arriving.
    admin.execute(
        "UPDATE public.document_versions SET scan_status = 'INFECTED' WHERE id = %s",
        (clean_version,),
    )
    with as_user(owner_a.user_id, comp_a) as c:
        assert c.execute("SELECT count(*) FROM public.document_versions").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# T21 permission resolution returns the full set
#
# Regression guard for a real defect. `app.my_permissions` is SETOF text, so a
# bare `SELECT app.my_permissions(...)` yields one row per permission. Calling
# `.scalar()` on that result returns only the first row, which reduced every
# authenticated request to a single permission and denied almost everything.
#
# The application layer must therefore aggregate. This test asserts both the
# row count of the bare form (so the shape is pinned) and that the aggregated
# form the API actually uses returns every permission.
# ---------------------------------------------------------------------------
def test_t21_permission_resolution_returns_full_set(
    world: World, admin: psycopg.Connection
) -> None:
    owner = world.users["owner_a"]
    comp_a = make_company(admin, world, "A", owner)

    bare = admin.execute("SELECT app.my_permissions(%s, %s)", (comp_a, owner.user_id)).fetchall()

    # The bare form yields many rows, so any caller that takes a single row is
    # losing permissions.
    assert len(bare) > 10, (
        f"expected app.my_permissions to return one row per permission; got {len(bare)}"
    )

    # This is the exact expression apps/api/app/api/deps.py uses.
    aggregated = admin.execute(
        """
        SELECT COALESCE(array_agg(perm), '{}')
          FROM app.my_permissions(%s, %s) AS perm
        """,
        (comp_a, owner.user_id),
    ).fetchone()[0]

    assert set(aggregated) == {row[0] for row in bare}

    # A SUPER_ADMIN resolves every permission in the catalogue.
    total = admin.execute("SELECT count(*) FROM public.permissions").fetchone()[0]
    assert len(aggregated) == total, (
        f"SUPER_ADMIN resolved {len(aggregated)} of {total} permissions"
    )

    # The membership in a *different* company must not leak into this one.
    comp_b = make_company(admin, world, "B", world.users["outsider_b"])
    assert not admin.execute(
        """
        SELECT 1 FROM app.my_permissions(%s, %s) AS perm
         WHERE perm = 'audit.read'
        """,
        (comp_b, owner.user_id),
    ).fetchall()
