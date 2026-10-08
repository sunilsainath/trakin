"""Probe Supabase connection-pooler regions for a reachable IPv4 endpoint.

Supabase's direct DB host is often IPv6-only. The transaction pooler
(aws-0-<region>.pooler.supaperabase.com -> aws-0-<region>.pooler.supabase.com)
is reachable over IPv4. This script finds the region for a project ref.

Usage:
    python scripts/find_pooler.py <project-ref> <db-password>
"""

from __future__ import annotations

import socket
import sys
from urllib.parse import quote_plus

REGIONS = [
    "us-east-1", "us-east-2", "us-west-1", "us-west-2", "ca-central-1",
    "sa-east-1", "eu-west-1", "eu-west-2", "eu-west-3", "eu-central-1",
    "eu-central-2", "ap-south-1", "ap-northeast-1", "ap-northeast-2",
    "ap-southeast-1", "ap-southeast-2", "ap-southeast-3", "me-central-1",
    "af-south-1",
]


def reachable(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2

    ref = sys.argv[1]
    # The password routinely contains '@', ':' and '/', each of which breaks a
    # libpq URI unless percent-encoded.
    password = quote_plus(sys.argv[2]) if len(sys.argv) > 2 else ""

    for region in REGIONS:
        host = f"aws-0-{region}.pooler.supabase.com"
        for port, mode in ((6543, "transaction"), (5432, "session")):
            if not reachable(host, port):
                continue
            url = f"postgresql://postgres.{ref}:{password}@{host}:{port}/postgres"
            try:
                import psycopg

                with psycopg.connect(url, connect_timeout=10) as conn:
                    row = conn.execute(
                        "SELECT current_setting('server_version'), inet_server_addr()"
                    ).fetchone()
                print(f"CONNECTED mode={mode} region={region} host={host} port={port}")
                print(f"  version={row[0]} server_addr={row[1]}")
                print(f"  DATABASE_URL={url}")
                return 0
            except Exception as exc:  # noqa: BLE001
                print(f"  tcp open but connect failed {host}:{port} -> {exc}")

    print("no reachable pooler region found", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
