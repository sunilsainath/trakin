"""Ad-hoc SQL runner for verifying the schema against a live database.

Usage:
    python scripts/sql.py "SELECT 1"
"""

from __future__ import annotations

import os
import sys

import psycopg


def main() -> int:
    dsn = os.environ.get("DATABASE_ADMIN_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        print("set DATABASE_ADMIN_URL", file=sys.stderr)
        return 2
    sql = sys.argv[1]
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(sql)
            if cur.description is None:
                print("(no result)")
                return 0
            rows = cur.fetchall()
            if not rows:
                print("(0 rows)")
                return 0
            width = max(len(k) for k in rows[0])
            for row in rows:
                print(" | ".join(f"{k:<{width}}={v}" for k, v in row.items()))
            print(f"\n({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
