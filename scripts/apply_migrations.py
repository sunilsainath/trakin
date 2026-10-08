"""Apply every supabase/migrations file in order, reporting per-file success.

Usage:
    python scripts/apply_migrations.py [--reset] [--dsn postgresql://...]
                               [--force] [--only 0015] [--status]

This is the same work `supabase db push` performs, but it lets us verify the
schema against a real PostgreSQL instance from CI or a workstation.

Applied files are recorded in `platform.schema_migrations`, so re-running is
safe even though the historical migration files are written for a single,
forward-only pass. `--force` replays everything; `--reset` drops the schemas
first, which invalidates the ledger.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "supabase" / "migrations"

LEDGER = """
CREATE SCHEMA IF NOT EXISTS platform;
CREATE TABLE IF NOT EXISTS platform.schema_migrations (
    version     text PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now(),
    duration_ms int NOT NULL DEFAULT 0
);
"""

LEDGER_QUERY = "SELECT version FROM platform.schema_migrations"


def dsn() -> str:
    return (
        os.environ.get("DATABASE_ADMIN_URL")
        or os.environ.get("DATABASE_URL")
        or "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
    )


def apply(
    reset: bool, *, force: bool = False, only: str | None = None, status_only: bool = False
) -> int:
    files = sorted(MIGRATIONS.glob("*.sql"))
    if not files:
        print("no migrations found", file=sys.stderr)
        return 1
    if only:
        files = [p for p in files if p.name.startswith(only)]
        if not files:
            print(f"no migration matching {only!r}", file=sys.stderr)
            return 1

    failures = 0
    applied = 0
    skipped = 0
    connect_timeout = int(os.environ.get("PGCONNECT_TIMEOUT", "60"))

    with psycopg.connect(dsn(), autocommit=True, connect_timeout=connect_timeout) as conn:
        conn.execute(LEDGER)
        done = {str(r[0]) for r in conn.execute(LEDGER_QUERY).fetchall()}  # type: ignore[union-attr]

        if reset:
            print("resetting public schema ...")
            conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
            conn.execute("DROP SCHEMA IF EXISTS app CASCADE")
            conn.execute("DROP SCHEMA IF EXISTS platform CASCADE")
            conn.execute("CREATE SCHEMA public")
            for ext in ("pgcrypto", "vector", "pg_trgm", "btree_gist", "unaccent"):
                conn.execute(f"CREATE EXTENSION IF NOT EXISTS {ext}")  # noqa: S608 - fixed literal set
            # `platform` went with the drop; 0001 recreates it with IF NOT EXISTS,
            # so only the ledger needs restoring.
            conn.execute(LEDGER)
            done = set()

        for path in files:
            version = path.name
            if version in done and not force:
                print(f"skip {version} (already applied)")
                skipped += 1
                continue
            if status_only:
                continue

            sql = path.read_text(encoding="utf-8")
            started = time.perf_counter()
            try:
                with conn.transaction():
                    conn.execute(sql)
                    conn.execute(
                        """
                        INSERT INTO platform.schema_migrations (version, duration_ms)
                        VALUES (%s, %s)
                        ON CONFLICT (version) DO UPDATE
                           SET applied_at = now(), duration_ms = EXCLUDED.duration_ms
                        """,
                        (version, 0),
                    )
            except Exception as exc:  # noqa: BLE001 - report and continue
                failures += 1
                print(f"FAIL {version}: {type(exc).__name__}: {exc}")
                continue

            elapsed = int((time.perf_counter() - started) * 1000)
            conn.execute(
                "UPDATE platform.schema_migrations SET duration_ms = %s WHERE version = %s",
                (elapsed, version),
            )
            applied += 1
            print(f"ok   {version} ({elapsed}ms)")

    if status_only:
        print(f"\napplied: {len(done)} / {len(files)} migrations present in the database")
        return 0

    print(
        f"\n{applied} applied, {skipped} already current, {failures} failed "
        f"(of {len(files)} migrations)"
    )
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="drop schemas first")
    parser.add_argument("--force", action="store_true", help="replay already-applied migrations")
    parser.add_argument("--only", default=None, help="apply migrations whose name starts with this")
    parser.add_argument("--status", action="store_true", help="report the ledger and exit")
    parser.add_argument("--dsn", default=None)
    args = parser.parse_args()
    if args.dsn:
        os.environ["DATABASE_ADMIN_URL"] = args.dsn
    return apply(
        args.reset, force=args.force, only=args.only, status_only=args.status
    )


if __name__ == "__main__":
    raise SystemExit(main())