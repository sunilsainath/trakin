"""Remove the trusted-context guard from public-id assignment functions.

These generators must run for every writer, including superusers running
migrations and seeds. A misplaced guard makes public_id stay NULL, which trips the
NOT NULL constraint.

Also asserts that no function whose name starts with app.assign_ or
app.sync_ still contains a guard.

Idempotent. Usage:
    python scripts/fix_public_id_guards.py
"""

from __future__ import annotations

import re
from pathlib import Path

BLOCK = (
    "  -- Migrations, seeds and background workers have no end-user identity.\n"
    "  -- mytrakin_api is neither superuser nor BYPASSRLS, so request traffic\n"
    "  -- always falls through to the real checks below.\n"
    "  IF app.is_trusted_context() THEN\n"
    "    RETURN NEW;\n"
    "  END IF;\n"
)

GENERATOR = re.compile(r"CREATE OR REPLACE FUNCTION app\.(assign_\w+|sync_\w+)\(")
HEAD = re.compile(
    r"CREATE OR REPLACE FUNCTION ([\w.]+)\([^)]*\)(?P<rest>[^$]*?)AS \$\$",
    re.DOTALL,
)


def main() -> int:
    total = 0
    problems: list[str] = []

    for path in sorted(Path("supabase/migrations").glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        changed = True
        rounds = 0
        while changed and rounds < 60:
            changed = False
            rounds += 1
            for m in HEAD.finditer(text):
                name = m.group(1)
                if not (name.startswith("app.assign_") or name.startswith("app.sync_")):
                    continue
                bs = m.end()
                be = text.find("$$;", bs)
                if be == -1:
                    continue
                body = text[bs:be]
                if BLOCK in body:
                    text = text[:bs] + body.replace(BLOCK, "", 1) + text[be:]
                    path.write_text(text, encoding="utf-8")
                    total += 1
                    changed = True
                    break

        # Verify no generator still holds a guard.
        for m in HEAD.finditer(text):
            name = m.group(1)
            if not (name.startswith("app.assign_") or name.startswith("app.sync_")):
                continue
            bs = m.end()
            be = text.find("$$;", bs)
            if be != -1 and "is_trusted_context" in text[bs:be]:
                problems.append(f"{path.name}: {name}")

    if problems:
        print("generators still guarded:")
        for p in problems:
            print(f"  {p}")
        return 1

    print(f"removed {total} guard(s) from public-id generators; all clear")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
