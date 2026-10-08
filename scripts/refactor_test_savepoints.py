"""Rewrite `with pytest.raises(X): c.execute(...)` into savepoint-based checks.

A failed statement poisons a psycopg transaction, so every expected failure must
run inside a savepoint. This converts the pattern to:

    with expect_raises(c, psycopg.errors.X):
        c.execute(...)

Idempotent. Usage:
    python scripts/refactor_test_savepoints.py
"""

from __future__ import annotations

import re
from pathlib import Path

TEST = Path("apps/api/tests/test_rls_security.py")

# with pytest.raises(EXC):\n<indent>c.execute( ... )\n<dedent>
PATTERN = re.compile(
    r"^(?P<indent>[ ]+)with pytest\.raises\((?P<exc>[\w.]+)\):\n"
    r"(?P<body>(?:(?P=indent)[ ]{4}[^\n]*\n?)+)",
    re.MULTILINE,
)


def main() -> int:
    text = TEST.read_text(encoding="utf-8")

    def repl(m: re.Match[str]) -> str:
        indent = m.group("indent")
        exc = m.group("exc")
        body = m.group("body")
        # de-indent the body by 4 spaces so it nests one level under the helper
        new_body = "\n".join(
            (line[4:] if line.startswith(indent + "    ") else line)
            for line in body.split("\n")
        )
        return f"{indent}with expect_raises(c, {exc}):\n{new_body}"

    new_text, n = PATTERN.subn(repl, text)
    if n:
        TEST.write_text(new_text, encoding="utf-8")
    print(f"converted {n} pytest.raises block(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
