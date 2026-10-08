"""Print the deployed source of a database function with line numbers."""

from __future__ import annotations

import os
import sys

import psycopg


def main() -> None:
    name = sys.argv[1]
    schema = sys.argv[2] if len(sys.argv) > 2 else "public"
    dsn = os.environ.get("DATABASE_ADMIN_URL") or os.environ["DATABASE_URL"]

    with psycopg.connect(dsn, autocommit=True) as conn:
        row = conn.execute(
            "SELECT prosrc FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE p.proname = %s AND n.nspname = %s",
            (name, schema),
        ).fetchone()

    if row is None:
        print(f"{schema}.{name} not found")
        return

    for i, line in enumerate(row[0].split("\n"), start=1):
        print(f"{i:3}: {line}")


if __name__ == "__main__":
    main()
