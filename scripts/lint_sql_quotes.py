"""Detect malformed single-quoted SQL literals in migration files.

The real bug this catches: an unescaped apostrophe inside a string literal, which
PostgreSQL then parses as the end of the literal. Symptom:

    COMMENT ON COLUMN x IS 'UNIQUE(a,b) makes ...';   -- `)` after `'` ends the literal

A `--` comment containing an apostrophe is perfectly legal SQL and is NOT
reported; only literals that swallow the following token are.

Usage:
    python scripts/lint_sql_quotes.py supabase/migrations
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

DOLLAR_TAG = re.compile(r"\$[a-zA-Z_][a-zA-Z_0-9]*\$|\$\$")
# A closing quote must be followed by a legal token boundary, otherwise the
# literal actually ended earlier and the remaining characters are a syntax error.
# `::` is legal immediately after a literal (e.g. '{}'::jsonb), and `:` also
# appears inside longer operators and in PL/pgSQL labels, so it is allowed.
VALID_FOLLOW = re.compile(r"[\s,;)\]:]")


def scan(path: Path) -> list[str]:
    problems: list[str] = []
    text = path.read_text(encoding="utf-8")
    i, line, n = 0, 1, len(text)

    while i < n:
        ch = text[i]

        if ch == "\n":
            line += 1
            i += 1
            continue

        if text.startswith("--", i):
            j = text.find("\n", i)
            i = n if j == -1 else j
            continue

        if text.startswith("/*", i):
            depth, i = 1, i + 2
            while i < n and depth:
                if text.startswith("/*", i):
                    depth += 1
                    i += 2
                elif text.startswith("*/", i):
                    depth -= 1
                    i += 2
                else:
                    if text[i] == "\n":
                        line += 1
                    i += 1
            continue

        m = DOLLAR_TAG.match(text, i)
        if m:
            tag = m.group(0)
            j = text.find(tag, m.end())
            if j == -1:
                problems.append(f"{path.name}:{line}: unterminated dollar-quote {tag}")
                break
            line += text.count("\n", i, j + len(tag))
            i = j + len(tag)
            continue

        if ch == "'":
            start_line = line
            j = i + 1
            closed = False
            while j < n:
                if text[j] == "\n":
                    line += 1
                    j += 1
                    continue
                if text[j] == "'":
                    if j + 1 < n and text[j + 1] == "'":
                        j += 2
                        continue
                    closed = True
                    break
                j += 1

            if not closed:
                problems.append(f"{path.name}:{start_line}: unterminated string literal")
                break

            after = text[j + 1: j + 2]
            if after and not VALID_FOLLOW.match(after):
                line_start = text.rfind("\n", 0, i) + 1
                line_end = text.find("\n", i)
                line_text = text[line_start: line_end if line_end != -1 else len(text)]
                problems.append(
                    f"{path.name}:{start_line}: literal terminates before an identifier or "
                    f"operator (next char {after!r}) — unescaped apostrophe?\n"
                    f"      {line_text.strip()[:100]}"
                )
            i = j + 1
            continue

        i += 1

    return problems


def main() -> int:
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "supabase/migrations")
    files = sorted(target.glob("*.sql")) if target.is_dir() else [target]

    problems: list[str] = []
    for f in files:
        problems.extend(scan(f))

    if problems:
        print("SQL literal problems:\n")
        for p in problems:
            print(f"  {p}")
        return 1

    print(f"ok: {len(files)} files, no literal problems")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
