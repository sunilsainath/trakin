"""Remove the trusted-context guard from the RBAC audit triggers.

`audit_role_permission_change` and `audit_membership_change` must never skip.
Their whole purpose is to record a privilege change, and a skipped record is
indistinguishable from a change that never happened. The guard belongs only in
functions that perform an *authorization decision*; auditing is unconditional.

Idempotent. Usage:
    python scripts/clean_audit_guards.py
"""

from __future__ import annotations

from pathlib import Path

BLOCK = (
    "\n  -- Migrations, seeds and background workers have no end-user identity.\n"
    "  -- mytrakin_api is neither superuser nor BYPASSRLS, so request traffic\n"
    "  -- always falls through to the real checks below.\n"
    "  IF app.is_trusted_context() THEN\n"
    "    RETURN NEW;\n"
    "  END IF;\n"
)

TARGETS = (
    ("0004_rbac_companies.sql", "app.audit_role_permission_change"),
    ("0004_rbac_companies.sql", "app.audit_membership_change"),
)


def main() -> int:
    total = 0
    for filename, func in TARGETS:
        path = Path("supabase/migrations") / filename
        text = path.read_text(encoding="utf-8")
        head = f"CREATE OR REPLACE FUNCTION {func}("
        idx = text.find(head)
        if idx == -1:
            print(f"  SKIP {func}: not found")
            continue
        body_start = text.find("$$", idx)
        if body_start == -1:
            continue
        body_end = text.find("$$;", body_start)
        if body_end == -1:
            continue
        body = text[body_start:body_end]
        if BLOCK in body:
            text = text[:body_start] + body.replace(BLOCK, "\n", 1) + text[body_end:]
            path.write_text(text, encoding="utf-8")
            total += 1
            print(f"  cleaned {func}")
        else:
            print(f"  already clean: {func}")
    print(f"\n{total} audit guard(s) removed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
