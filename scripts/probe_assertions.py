"""Focused probes for the assertions that failed in the security suite."""

from __future__ import annotations

import os
import uuid

import psycopg

DSN = os.environ["DATABASE_ADMIN_URL"]
API = "mytrakin_api"


def probe(name: str, sql: str, params: tuple = ()) -> None:
    try:
        with psycopg.connect(DSN, autocommit=True) as c:
            c.execute("GRANT mytrakin_api TO CURRENT_USER")
            cur = c.execute(sql, params)
            rows = cur.fetchall() if cur.description else []
            print(f"  {name}: {rows}")
    except Exception as exc:  # noqa: BLE001
        print(f"  {name}: {type(exc).__name__}: {exc}")


def main() -> None:
    print("=== 1. append-only triggers present? ===")
    probe(
        "immutable triggers",
        """
        SELECT c.relname, t.tgname
          FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
         WHERE NOT t.tgisinternal
           AND (t.tgname LIKE '%immutable%' OR t.tgname LIKE '%hold%'
                OR t.tgname LIKE '%match_guard%' OR t.tgname LIKE '%txn_%')
         ORDER BY 1,2
        """,
    )

    print("\n=== 2. does SECURITY DEFINER survive FORCE RLS? ===")
    probe(
        "company_roles updated_at via trigger",
        """
        DO $$
        DECLARE c uuid; r uuid;
        BEGIN
          SELECT id INTO c FROM public.companies LIMIT 1;
          IF c IS NULL THEN RAISE NOTICE 'no companies'; RETURN; END IF;
          SELECT id INTO r FROM public.company_roles WHERE company_id = c LIMIT 1;
          RAISE NOTICE 'company=%, role=%', c, r;
        END $$;
        """,
    )

    print("\n=== 3. RLS child FK check constraints ===")
    probe(
        "invoice_items source constraint",
        """
        SELECT pg_get_constraintdef(oid)
          FROM pg_constraint
         WHERE conname = 'ck_invoice_item_source'
        """,
    )

    print("\n=== 4. project_roles referencing FKs ===")
    probe(
        "refs to project_roles",
        """
        SELECT conrelid::regclass::text AS child, confdeltype
          FROM pg_constraint
         WHERE confrelid = 'public.project_roles'::regclass
        """,
    )

    print("\n=== 5. timesheet totals recompute ===")
    probe(
        "update company_settings to force trigger",
        "SELECT count(*) FROM public.companies",
    )


if __name__ == "__main__":
    main()
