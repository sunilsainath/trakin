"""Local .env loader for the API.

Reads the repository-root .env, expanding ${VAR} references from the process
environment so DATABASE_ADMIN_URL and friends can be overridden per invocation
without editing the file.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"

LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def load(path: Path = ENV_FILE) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = LINE.match(raw)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        # ${VAR} / ${VAR:-default}
        def expand(match: re.Match[str]) -> str:
            name = match.group(1)
            fallback = match.group(3)
            return os.environ.get(name) or (fallback or "")

        value = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}", expand, value)
        values[key] = value
    return values


def main() -> int:
    for key, value in load().items():
        os.environ.setdefault(key, value)
        print(f"  {key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
