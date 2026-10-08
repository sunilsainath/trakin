"""Probe COMMENT ON behaviour against the live database."""

from __future__ import annotations

import os

import psycopg

QUERIES = [
    "select relname, relkind, relnamespace::regnamespace::text as ns "
    "from pg_class where relname in ('leave_balances', 'leave_policies', 'users')",
    "COMMENT ON TABLE public.leave_balances IS 'probe table comment'",
    "COMMENT ON COLUMN public.leave_balances.year IS 'probe column comment'",
    "COMMENT ON COLUMN leave_balances.year IS 'probe unqualified'",
    "select col_description('public.leave_balances'::regclass, attnum) "
    "from pg_attribute where attrelid = 'public.leave_balances'::regclass "
    "and attname = 'year'",
    "select current_user, rolsuper, rolbypassrls, r.rolname "
    "from pg_roles r where r.rolname = current_user",
    "show search_path",
]


def main() -> None:
    dsn = os.environ["DATABASE_ADMIN_URL"]
    with psycopg.connect(dsn, autocommit=True) as conn:
        for sql in QUERIES:
            try:
                cur = conn.execute(sql)
                rows = cur.fetchall() if cur.description else []
                print(f"OK   {sql[:70]}")
                for r in rows:
                    print(f"       {r}")
            except Exception as exc:  # noqa: BLE001
                print(f"FAIL {sql[:70]}\n       {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
