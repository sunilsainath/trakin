"""Verify the live database matches the schema the application expects.

`alembic check` cannot serve this purpose: the API uses hand-written SQL rather
than ORM models, so there is no `MetaData` for it to compare against. Instead
this script asserts that every database object the application actually depends
on exists, with the properties the application assumes.

It is a contract test for the database, not a migration runner: it never writes.

Usage:
    set DATABASE_ADMIN_URL=...
    python scripts/verify_schema.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from load_env import load  # noqa: E402

# Objects the API and the RLS policies depend on, grouped by why they matter.
REQUIRED_TABLES = {
    "platform": [
        "audit_logs",
        "outbox_events",
        "idempotency_keys",
        "feature_flags",
        "feature_flag_overrides",
        "notifications",
        "notification_preferences",
        "tasks",
        "rate_limit_counters",
        "user_sessions",
    ],
    "public": [
        "users",
        "user_profiles",
        "user_privacy",
        "user_sensitive",
        "companies",
        "company_roles",
        "company_memberships",
        "role_permissions",
        "permissions",
        "company_invitations",
        "projects",
        "sows",
        "contracts",
        "timesheets",
        "timesheet_entries",
        "assignments",
        "invoices",
        "invoice_items",
        "payments",
        "bank_transactions",
        "documents",
        "document_versions",
        "msas",
        "ai_actions",
        "ai_document_chunks",
        "search_index",
    ],
}

REQUIRED_FUNCTIONS = [
    "app.current_user_id",
    "app.current_company_id",
    "app.current_request_id",
    "app.has_permission",
    "app.my_permissions",
    "app.effective_role_keys",
    "app.gen_public_id",
    "app.is_blocked",
    "app.bootstrap_company_roles",
]

REQUIRED_ROLES = ["mytrakin_api", "mytrakin_worker"]

# Ledger tables reject mutation through a trigger rather than by withholding the
# privilege, because legitimate reconciliation flows need UPDATE on some of them.
# The invariant that matters is that a row cannot be changed after the fact.
APPEND_ONLY_GUARDS = {
    "platform.audit_logs": "trg_audit_immutable",
    "public.bank_transactions": "trg_txn_10_ledger_facts",
}


def dsn() -> str:
    for key, value in load().items():
        os.environ.setdefault(key, value)
    return (
        os.environ.get("DATABASE_ADMIN_URL")
        or os.environ.get("DATABASE_URL")
        or "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
    )


class Report:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.passes: list[str] = []

    def check(self, ok: bool, description: str, detail: str = "") -> None:
        if ok:
            self.passes.append(description)
        else:
            self.failures.append(f"{description}{f' ({detail})' if detail else ''}")


def main() -> int:
    report = Report()

    with psycopg.connect(dsn(), autocommit=True) as conn:
        # `prepare_threshold=None` disables the prepared-statement cache. This
        # script runs many distinct queries with identical placeholder shapes, and
        # autocommit plus a long-lived connection otherwise trips a
        # DuplicatePreparedStatement error on some poolers.
        conn.prepare_threshold = None

        for schema, tables in REQUIRED_TABLES.items():
            present = {
                row[0]
                for row in conn.execute(
                    """
                    SELECT table_name FROM information_schema.tables
                     WHERE table_schema = %s
                    """,
                    (schema,),
                ).fetchall()
            }
            for table in tables:
                report.check(table in present, f"table {schema}.{table} exists")

        for function in REQUIRED_FUNCTIONS:
            row = conn.execute(
                "SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname || '.' || p.proname = %s",
                (function,),
            ).fetchone()
            report.check(row is not None, f"function {function} exists")

        for role in REQUIRED_ROLES:
            row = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
            report.check(row is not None, f"role {role} exists")

        # The API role must never be able to bypass RLS.
        row = conn.execute(
            "SELECT rolsuper, rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname = 'mytrakin_api'"
        ).fetchone()
        if row is not None:
            report.check(not row[0], "role mytrakin_api is not a superuser")
            report.check(not row[1], "role mytrakin_api cannot bypass RLS")

        # mytrakin_worker is created BYPASSRLS on purpose: Celery tasks run outside
        # a request and have no company identity to set. NOLOGIN is what makes that
        # safe — the role can only be reached through an explicit SET ROLE from an
        # already-authenticated session, never by a direct connection.
        row = conn.execute(
            "SELECT rolcanlogin, rolbypassrls FROM pg_roles WHERE rolname = 'mytrakin_worker'"
        ).fetchone()
        if row is not None:
            report.check(not row[0], "role mytrakin_worker cannot log in directly")
            report.check(row[1], "role mytrakin_worker is BYPASSRLS as designed")

        # Every tenant table must have RLS enabled.
        for schema, tables in REQUIRED_TABLES.items():
            enabled = {
                row[0]
                for row in conn.execute(
                    "SELECT c.relname FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = %s AND c.relrowsecurity",
                    (schema,),
                ).fetchall()
            }
            for table in tables:
                # Platform tables are not tenant-scoped; only public ones are.
                if schema != "public":
                    continue
                report.check(
                    table in enabled,
                    f"RLS enabled on {schema}.{table}",
                    "row security is off",
                )

        # Ledger and audit tables must carry an immutability guard.
        for table, trigger in APPEND_ONLY_GUARDS.items():
            found = conn.execute(
                """
                SELECT 1
                  FROM pg_trigger t
                  JOIN pg_class c ON c.oid = t.tgrelid
                  JOIN pg_namespace n ON n.oid = c.relnamespace
                 WHERE n.nspname || '.' || c.relname = %s
                   AND t.tgname = %s
                   AND NOT t.tgisinternal
                """,
                (table, trigger),
            ).fetchone()
            report.check(found is not None, f"{table} has guard trigger {trigger}")

        # The permission catalogue must be populated, or nothing is authorised.
        count = conn.execute("SELECT count(*) FROM public.permissions").fetchone()[0]
        report.check(count > 0, "permission catalogue is populated", f"{count} rows")

        # Role templates are the source a new company's roles are built from.
        # Company roles themselves are created per company at bootstrap time, so
        # this table is the one that must never be empty.
        templates = conn.execute(
            "SELECT count(*) FROM public.role_templates WHERE is_default"
        ).fetchone()[0]
        report.check(templates > 0, "default role templates exist", f"{templates} rows")

    for description in report.passes:
        print(f"  PASS  {description}")

    if report.failures:
        print()
        for failure in report.failures:
            print(f"  FAIL  {failure}", file=sys.stderr)
        print(f"\n{len(report.failures)} check(s) failed", file=sys.stderr)
        return 1

    print(f"\n{len(report.passes)}/{len(report.passes)} schema checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())