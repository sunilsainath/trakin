"""Check that permission resolution returns every permission, not just the first.

Regression guard for a real defect: app.my_permissions is SETOF text, so
`SELECT app.my_permissions(...)` yields one row per permission and `.scalar()`
returns only the first one, which reduced a caller to a single permission.

Usage:
    set DATABASE_ADMIN_URL=...
    python scripts/check_permission_resolution.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from load_env import load as load_env  # noqa: E402


def dsn() -> str:
    return (
        os.environ.get("DATABASE_ADMIN_URL")
        or os.environ.get("DATABASE_URL")
        or "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
    )


def main() -> int:
    # load() returns the values; it does not populate os.environ itself.
    for key, value in load_env().items():
        os.environ.setdefault(key, value)

    with psycopg.connect(dsn(), autocommit=True) as conn:
        # Join through the role so a membership left behind by a test run
        # (one whose role row no longer exists) cannot be picked here.
        row = conn.execute(
            """
            SELECT m.company_id::text AS company_id, m.user_id::text AS user_id
              FROM public.company_memberships m
              JOIN public.company_roles r ON r.id = m.role_id AND r.deleted_at IS NULL
             WHERE m.status = 'ACTIVE'
               AND EXISTS (
                     SELECT 1 FROM public.role_permissions rp
                      WHERE rp.role_id = r.id)
             LIMIT 1
            """
        ).fetchone()

        if row is None:
            print("SKIP: no active membership exists to test against", file=sys.stderr)
            return 0

        company_id, user_id = row

        naive = conn.execute(
            "SELECT app.my_permissions(%s::uuid, %s::uuid)", (company_id, user_id)
        ).fetchall()

        aggregated = conn.execute(
            """
            SELECT COALESCE(array_agg(perm), '{}')
              FROM app.my_permissions(%s::uuid, %s::uuid) AS perm
            """,
            (company_id, user_id),
        ).fetchone()[0]

        print(f"company {company_id}")
        print(f"  bare SELECT returns {len(naive)} row(s)")
        print(f"  array_agg returns {len(aggregated)} permission(s)")

        if len(aggregated) <= 1:
            print(
                "FAIL: aggregation did not resolve more than one permission; "
                "the test membership may hold a single permission.",
                file=sys.stderr,
            )
            return 1

        if len(naive) == len(aggregated) and len(naive) == 1:
            print(
                "NOTE: both forms returned one row, so this run cannot distinguish "
                "the bug. Re-run against a membership holding several permissions."
            )

        for key in sorted(aggregated)[:10]:
            print(f"    {key}")
        if len(aggregated) > 10:
            print(f"    ... and {len(aggregated) - 10} more")

        print("PASS")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())