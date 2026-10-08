"""Public-id allocation, format and immutability, asserted against the database.

The `public_id` is the only identifier a client ever sees, so three properties
have to hold for every tenant-scoped table:

1. a row inserted without one still gets one;
2. the identifier matches the format the table's own CHECK constraint (or, where
   a table has no CHECK, the format its `app.assign_*` trigger generates);
3. it is frozen — `app.freeze_public_id` refuses an UPDATE.

The regexes are not hard-coded here. They are read out of `pg_constraint` (and,
for the tables that carry no CHECK, out of the trigger function body that calls
`app.gen_public_id`), so the test cannot drift away from the schema.

Marked `integration`: needs the real database.

Run:
    pytest apps/api/tests/test_id_generation.py -v
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

if TYPE_CHECKING:
    from tests.conftest import Skeleton

pytestmark = pytest.mark.integration

#: The alphabet every generated id draws from. Taken from `app.gen_public_id`,
#: which maps random bytes onto Crockford-style base32 and therefore excludes
#: I, L, O and U.
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

#: Tables under test, and the columns each needs beyond `id`.
TABLES: dict[str, dict[str, Any]] = {
    "projects": {"sql": "company_id, name, status", "values": "(:c, :title, 'ACTIVE')"},
    "project_roles": {
        "sql": "project_id, company_id, title, required_count",
        "values": "(:p, :c, :title, 5)",
    },
    "sows": {
        "sql": ("project_id, company_id, sow_type, counterparty_company_id, title, status"),
        "values": "(:p, :c, 'COMPANY', :cp, :title, 'ACTIVE')",
    },
    "contracts": {
        "sql": (
            "sow_id, project_id, company_id, contract_type, counterparty_company_id, title, status"
        ),
        "values": "(:s, :p, :c, 'COMPANY', :cp, :title, 'ACTIVE')",
    },
    "invoices": {
        "sql": (
            "direction, company_id, counterparty_company_id, contract_id, period_start, "
            "period_end, issue_date, due_date, currency, status"
        ),
        "values": "('RECEIVABLE', :c, :cp, :ct, :ps, :pe, :ps, :pe, 'USD', 'DRAFT')",
    },
    "leave_policies": {
        "sql": "company_id, name, leave_type, accrual_method, effective_from",
        "values": "(:c, :title, 'ANNUAL', 'MONTHLY', :ps)",
    },
    "bank_accounts": {
        "sql": (
            "bank_connection_id, company_id, institution_name, account_number_masked, "
            "account_type, status"
        ),
        "values": "(:bc, :c, 'Test Bank', '****0001', 'CHECKING', 'CONNECTED')",
    },
    "documents": {
        "sql": "company_id, doc_type, title",
        "values": "(:c, 'OTHER', :title)",
    },
    "msas": {
        "sql": "company_a_id, company_b_id, status, effective_date, expiration_date",
        "values": "(:c, :cp, 'NO_MSA', :ps, :pe)",
    },
    "payments": {
        "sql": (
            "company_id, direction, amount, currency, payment_method, status, "
            "authorization_type, processor, processor_payment_ref"
        ),
        "values": (
            "(:c, 'RECEIVABLE', 100.00, 'USD', 'ACH', 'COMPLETED', 'EXPLICIT', 'MANUAL', "
            "'idgen-' || :title)"
        ),
    },
    "timesheets": {
        "sql": (
            "user_id, company_id, assignment_id, contract_id, project_id, period_start, "
            "period_end, status"
        ),
        "values": "(:u, :c, :asg, :ct, :p, :ps, :pe, 'DRAFT')",
    },
    "leave_requests": {
        "sql": ("user_id, company_id, leave_policy_id, start_date, end_date, total_days, status"),
        "values": "(:u, :c, :lp, :ps, :ps, 1, 'PENDING')",
    },
}


async def _check_regexes(conn) -> dict[str, str]:
    """`table -> regex`, taken from the table's own public_id CHECK constraint."""
    rows = (
        (
            await conn.execute(
                text(
                    """
                    SELECT c.relname, pg_get_constraintdef(con.oid)
                      FROM pg_constraint con
                      JOIN pg_class c ON c.oid = con.conrelid
                      JOIN pg_namespace n ON n.oid = c.relnamespace
                     WHERE n.nspname = 'public' AND con.contype = 'c'
                       AND pg_get_constraintdef(con.oid) ILIKE '%public_id%'
                    """
                )
            )
        )
        .mappings()
        .all()
    )
    out: dict[str, str] = {}
    for row in rows:
        definition = row["pg_get_constraintdef"]
        match = re.search(r"'(\^[^']+)'", definition)
        if match:
            out[row["relname"]] = match.group(1)
    return out


async def _trigger_prefixes(conn) -> dict[str, str]:
    """`table -> prefix`, taken from the `app.gen_public_id('<P>', n)` call."""
    rows = (
        (
            await conn.execute(
                text(
                    """
                    SELECT c.relname,
                           p.proname,
                           pg_get_functiondef(p.oid) AS body
                      FROM pg_trigger t
                      JOIN pg_class c ON c.oid = t.tgrelid
                      JOIN pg_namespace n ON n.oid = c.relnamespace
                      JOIN pg_proc p ON p.oid = t.tgfoid
                     WHERE n.nspname = 'public' AND NOT t.tgisinternal
                       AND p.proname LIKE 'assign%public_id'
                       OR (n.nspname = 'public' AND NOT t.tgisinternal
                           AND p.proname = 'assign_leave_public_id')
                    """
                )
            )
        )
        .mappings()
        .all()
    )
    out: dict[str, str] = {}
    for row in rows:
        match = re.search(r"app\.gen_public_id\('([A-Z]+)',\s*(\d+)\)", row["body"])
        if match:
            out[row["relname"]] = match.group(1)
    return out


def _params(skeleton: Skeleton, tenants) -> dict[str, Any]:
    """Bind values for every `TABLES` entry, so one helper serves all of them."""
    from app.core.clock import utc_today

    today = utc_today()
    return {
        "c": tenants["admin"].company_id,
        "cp": tenants["worker"].company_id,
        "u": tenants["worker"].user_id,
        "p": skeleton.project,
        "s": skeleton.sow,
        "ct": skeleton.contract,
        "asg": skeleton.assignment,
        "lp": skeleton.leave_policy,
        "bc": skeleton.bank_connection,
        "ps": today - timedelta(days=40),
        "pe": today - timedelta(days=20),
    }


# --------------------------------------------------------------------- tests
async def test_expected_prefixes_come_from_the_schema(conn, skeleton, tenants) -> None:
    """The trigger functions allocate P/R/S/C/I/... and nothing else."""
    prefixes = await _trigger_prefixes(conn)
    expected = {
        "projects": "P",
        "project_roles": "R",
        "sows": "S",
        "contracts": "C",
        "invoices": "I",
        "leave_policies": "LP",
        "bank_accounts": "BA",
        "documents": "D",
        "msas": "M",
        "payments": "PM",
        "timesheets": "TS",
        "leave_requests": "LR",
    }
    for table, prefix in expected.items():
        assert prefixes.get(table) == prefix, (
            f"{table}: expected the trigger to allocate a {prefix}... id, "
            f"got {prefixes.get(table)!r}"
        )


async def test_check_regexes_agree_with_the_generated_prefixes(conn) -> None:
    """Where a table has a CHECK, it must encode the same prefix the trigger uses."""
    checks = await _check_regexes(conn)
    prefixes = await _trigger_prefixes(conn)

    assert checks, "expected at least the five documented CHECK constraints"
    for table, regex in checks.items():
        compiled = re.compile(regex)
        assert compiled.match(_generate(regex)), f"{table}: {regex} rejects its own format"
        if table in prefixes:
            assert regex.startswith(f"^{prefixes[table]}"), (
                f"{table}: CHECK says {regex!r} but the trigger allocates {prefixes[table]}..."
            )


def _generate(regex: str) -> str:
    """A syntactically valid id for the given CHECK regex."""
    return re.sub(
        r"\[[^\]]+\]\{(\d+)\}",
        lambda m: "7" * int(m.group(1)),
        regex.lstrip("^").rstrip("$"),
    )


@pytest.mark.parametrize("table", sorted(TABLES))
async def test_insert_allocates_a_public_id_matching_the_schema(
    conn, skeleton, tenants, table: str
) -> None:
    """No public_id supplied -> the trigger allocates one in the declared format."""
    spec = TABLES[table]
    params = _params(skeleton, tenants)
    params.update({"title": f"ID generation {table}", "p": skeleton.project, "s": skeleton.sow})

    allocated = (
        (
            await conn.execute(
                text(
                    f"INSERT INTO public.{table} ({spec['sql']}) "
                    f"VALUES {spec['values']} RETURNING public_id"
                ),
                params,
            )
        )
        .mappings()
        .one()
    )["public_id"]

    assert allocated, f"{table}: the trigger left public_id NULL"
    assert isinstance(allocated, str)

    checks = await _check_regexes(conn)
    if table in checks:
        assert re.match(checks[table], allocated), (
            f"{table}: allocated {allocated!r} does not match its own CHECK {checks[table]!r}"
        )
    else:
        prefixes = await _trigger_prefixes(conn)
        prefix = prefixes[table]
        assert allocated.startswith(prefix), f"{table}: expected a {prefix}... id"
        assert re.fullmatch(rf"{prefix}[{ALPHABET}]{{8}}", allocated), (
            f"{table}: allocated {allocated!r} is not {prefix} plus 8 base32 characters"
        )


@pytest.mark.parametrize("table", sorted(TABLES))
async def test_repeated_inserts_do_not_collide(conn, skeleton, tenants, table: str) -> None:
    """Twelve rows, twelve distinct ids: the generator retries rather than clashing.

    Each row differs in whatever dimension the table makes unique — the billing
    period for invoices and timesheets, the counterparty for an MSA — so a
    collision reported here is genuinely a public_id collision and not a unique
    index firing first.
    """
    spec = TABLES[table]
    base = _params(skeleton, tenants)
    base.update({"title": f"ID generation {table}", "p": skeleton.project, "s": skeleton.sow})

    counterparties: list[Any] = []
    if table == "msas":
        # `msas` is unique per company pair, so a repeatable insert needs a
        # distinct counterparty company each time.
        counterparties = list(
            (
                await conn.execute(
                    text(
                        "INSERT INTO public.companies (legal_name, display_name, created_by)"
                        " SELECT 'ID generation peer ' || g, 'peer ' || g, CAST(:owner AS uuid)"
                        " FROM generate_series(1, 12) AS g RETURNING id"
                    ),
                    {"owner": tenants["admin"].user_id},
                )
            )
            .scalars()
            .all()
        )

    seen: list[str] = []
    for index in range(12):
        params = dict(
            base,
            title=f"ID generation {table} #{index}",
            ps=base["ps"] + timedelta(days=index * 3),
            pe=base["pe"] + timedelta(days=index * 3),
        )
        if counterparties:
            params["cp"] = counterparties[index]
        seen.append(
            str(
                (
                    await conn.execute(
                        text(
                            f"INSERT INTO public.{table} ({spec['sql']}) "
                            f"VALUES {spec['values']} RETURNING public_id"
                        ),
                        params,
                    )
                ).scalar_one()
            )
        )

    assert len(set(seen)) == len(seen), f"{table}: duplicate public_id allocated: {seen}"
    assert all(value for value in seen), f"{table}: a NULL public_id was allocated"


@pytest.mark.parametrize("table", sorted(TABLES))
async def test_public_id_is_frozen_against_update(
    api_conn, api_skeleton, tenants, table: str
) -> None:
    """`app.freeze_public_id` refuses the rewrite for every tenant-scoped table.

    Run as ``mytrakin_api`` rather than as the connection in ``DATABASE_URL``:
    ``app.freeze_public_id`` opens with ``IF app.is_trusted_context() THEN RETURN
    NEW``, and that helper is true for any role with BYPASSRLS. Without the
    ``SET LOCAL ROLE`` the guard is skipped and the test would prove nothing.
    """
    spec = TABLES[table]
    params = _params(api_skeleton, tenants)
    params.update({"title": f"freeze {table}", "p": api_skeleton.project, "s": api_skeleton.sow})

    row = (
        (
            await api_conn.execute(
                text(
                    f"INSERT INTO public.{table} ({spec['sql']}) "
                    f"VALUES {spec['values']} RETURNING id, public_id"
                ),
                params,
            )
        )
        .mappings()
        .one()
    )
    row_id, before = str(row["id"]), str(row["public_id"])

    replacement = "Z" + before[1:]
    # The SAVEPOINT matters: PostgreSQL aborts the whole transaction on any error,
    # so without it the verification read below would fail with
    # InFailedSqlTransaction instead of showing the identifier is unchanged.
    with pytest.raises(IntegrityError) as caught:
        async with api_conn.begin_nested():
            await api_conn.execute(
                text(f"UPDATE public.{table} SET public_id = :new WHERE id = :i"),
                {"new": replacement, "i": row_id},
            )
    assert "immutable" in str(caught.value), str(caught.value)

    after = str(
        (
            await api_conn.execute(
                text(f"SELECT public_id FROM public.{table} WHERE id = :i"), {"i": row_id}
            )
        ).scalar_one()
    )
    assert after == before


async def test_freeze_is_bypassed_only_for_a_trusted_context(conn, skeleton, tenants) -> None:
    """Documents the escape hatch `app.is_trusted_context()` opens.

    Migrations, seeds and the sync worker run with no end-user identity and are
    trusted; ``mytrakin_api`` is neither superuser nor BYPASSRLS and therefore
    always falls through to the check. Without this property the trigger would
    break every migration that backfills a public_id.
    """
    role = (
        await conn.execute(
            text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")
        )
    ).scalar_one()
    assert role is True, (
        "this test describes the behaviour of the DATABASE_URL role; if it is no longer "
        "BYPASSRLS the property it documents no longer applies"
    )

    row_id = str(
        (
            await conn.execute(
                text(
                    "INSERT INTO public.projects (company_id, name, status)"
                    " VALUES (:c, 'trusted rewrite', 'DRAFT') RETURNING id"
                ),
                {"c": tenants["admin"].company_id},
            )
        ).scalar_one()
    )
    await conn.execute(
        text("UPDATE public.projects SET public_id = 'PZZZZZZZZ' WHERE id = :i"), {"i": row_id}
    )
    rewritten = str(
        (
            await conn.execute(
                text("SELECT public_id FROM public.projects WHERE id = :i"), {"i": row_id}
            )
        ).scalar_one()
    )
    assert rewritten == "PZZZZZZZZ"


async def test_unknown_api_role_is_rejected(api_conn, tenants) -> None:
    """The test suite needs a real non-BYPASSRLS role; assert the impersonation works.

    If `mytrakin_api` were missing or the grant had lapsed, every
    database-enforcement test in this repository would silently degrade into a
    no-op, because the trusted-context guards would short-circuit.
    """
    bypass = (
        await api_conn.execute(
            text("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
        )
    ).scalar_one()
    assert bypass is False
    assert (await api_conn.execute(text("SELECT current_user"))).scalar_one() == "mytrakin_api"


async def test_public_id_must_match_the_check_constraint(conn, tenants) -> None:
    """A caller cannot smuggle in a malformed id that the trigger would not issue."""
    with pytest.raises(IntegrityError):
        await conn.execute(
            text(
                "INSERT INTO public.projects (company_id, name, status, public_id)"
                " VALUES (:c, 'bad id', 'DRAFT', 'NOT_A_PROJECT_ID')"
            ),
            {"c": tenants["admin"].company_id},
        )


async def test_public_id_is_unique_per_table(conn, skeleton, tenants) -> None:
    """Two rows cannot share an identifier, which is what makes it a usable key."""
    first = str(
        (
            await conn.execute(
                text(
                    "INSERT INTO public.projects (company_id, name, status)"
                    " VALUES (:c, 'unique one', 'DRAFT') RETURNING public_id"
                ),
                {"c": tenants["admin"].company_id},
            )
        ).scalar_one()
    )
    with pytest.raises(IntegrityError):
        await conn.execute(
            text(
                "INSERT INTO public.projects (company_id, name, status, public_id)"
                " VALUES (:c, 'unique two', 'DRAFT', :pid)"
            ),
            {"c": tenants["admin"].company_id, "pid": first},
        )
