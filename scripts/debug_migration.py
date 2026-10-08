"""Apply a migration file statement-by-statement to pinpoint the failing SQL.

Respects single-quoted literals, quoted identifiers, dollar-quoted bodies and
both comment styles, so a `;` inside a string never splits a statement.

Usage:
    python scripts/debug_migration.py supabase/migrations/0008_work_timesheets_leave.sql
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import psycopg

DOLLAR_TAG = re.compile(r"\$[a-zA-Z_][a-zA-Z_0-9]*\$|\$\$")


def split_statements(sql: str) -> list[str]:
    stmts: list[str] = []
    buf: list[str] = []
    i, n = 0, len(sql)
    tag: str | None = None
    in_string = in_ident = in_line = in_block = False
    depth = 0

    def flush() -> None:
        stmt = "".join(buf).strip()
        if stmt:
            stmts.append(stmt)
        buf.clear()

    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""

        if in_line:
            buf.append(ch)
            in_line = ch != "\n"
            i += 1
            continue

        if in_block:
            buf.append(ch)
            if ch == "/" and nxt == "*":
                buf.append(nxt); depth += 1; i += 2; continue
            if ch == "*" and nxt == "/":
                buf.append(nxt); depth -= 1; i += 2
                if depth == 0:
                    in_block = False
                continue
            i += 1
            continue

        if in_string:
            buf.append(ch)
            if ch == "'":
                if nxt == "'":
                    buf.append(nxt); i += 2; continue
                in_string = False
            i += 1
            continue

        if in_ident:
            buf.append(ch)
            in_ident = ch != '"'
            i += 1
            continue

        if tag is not None:
            if sql.startswith(tag, i):
                buf.append(tag); i += len(tag); tag = None; continue
            buf.append(ch); i += 1; continue

        if ch == "-" and nxt == "-":
            in_line = True; buf.append(ch); i += 1; continue
        if ch == "/" and nxt == "*":
            in_block = True; depth = 1; buf.append(ch); i += 1; continue

        m = DOLLAR_TAG.match(sql, i)
        if m:
            tag = m.group(0); buf.append(tag); i = m.end(); continue

        if ch == "'":
            in_string = True; buf.append(ch); i += 1; continue
        if ch == '"':
            in_ident = True; buf.append(ch); i += 1; continue
        if ch == ";":
            flush(); i += 1; continue

        buf.append(ch); i += 1

    flush()
    return stmts


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2

    dsn = os.environ.get("DATABASE_ADMIN_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        print("set DATABASE_ADMIN_URL", file=sys.stderr)
        return 2

    failures = 0
    for arg in sys.argv[1:]:
        path = Path(arg)
        stmts = split_statements(path.read_text(encoding="utf-8"))
        print(f"{path.name}: {len(stmts)} statements")
        with psycopg.connect(dsn, autocommit=True) as conn:
            for idx, stmt in enumerate(stmts, 1):
                try:
                    with conn.transaction():
                        conn.execute(stmt)
                except Exception as exc:  # noqa: BLE001
                    failures += 1
                    print(f"\n--- FAILED {path.name} statement {idx}/{len(stmts)} ---")
                    print(stmt[:600])
                    print(f"\n{type(exc).__name__}: {exc}")
                    break
            else:
                print(f"  ok: {path.name}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
