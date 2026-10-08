"""Remove the trusted-context guard from derivation and event trigger functions.

The guard exists to let superuser migrations and workers skip *authorization*
checks. It must never appear in a function that derives or emits state, because
skipping it silently produces wrong data (a project role whose allocation counter
stays 0, an invoice that never recalculates, an event that is never queued).

Affected function families:
  on_*     allocation maintenance
  refresh_* derived counters
  sync_*   denormalised mirrors
  compute_* derived amounts
  emit_*   outbox events
  advance_* scheduled-state helpers
  claim_*  webhook ledger
  normalize_* / build_* structural invariants

Authorization guards (assert_*, apply_leave_decision) keep the guard.
Idempotent. Usage:
    python scripts/clean_derivation_guards.py
"""

from __future__ import annotations

import re
from pathlib import Path

BLOCK = (
    "\n  -- Migrations, seeds and background workers have no end-user identity.\n"
    "  -- mytrakin_api is neither superuser nor BYPASSRLS, so request traffic\n"
    "  -- always falls through to the real checks below.\n"
    "  IF app.is_trusted_context() THEN\n"
    "    RETURN NEW;\n"
    "  END IF;\n"
)

HEAD = re.compile(
    r"CREATE OR REPLACE FUNCTION (?:app|platform)\.(?P<name>\w+)\([^)]*\)"
    r"(?P<rest>[^$]*?)AS \$\$",
    re.DOTALL,
)

DERIVATION = (
    "on_",
    "refresh_",
    "sync_",
    "compute_",
    "emit_",
    "advance_",
    "claim_",
    "normalize_",
    "build_",
    "apply_",  # only leave-decision style, which is an authorization check
)

# `apply_leave_decision` is an authorization check and must keep its guard.
KEEP_GUARD = {"apply_leave_decision"}


def main() -> int:
    total = 0
    for path in sorted(Path("supabase/migrations").glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        # Repeat because removing a block shifts offsets.
        for _ in range(80):
            changed = False
            for m in HEAD.finditer(text):
                name = m.group("name")
                if name in KEEP_GUARD:
                    continue
                if not name.startswith(DERIVATION):
                    continue
                bs = m.end()
                be = text.find("$$;", bs)
                if be == -1:
                    continue
                body = text[bs:be]
                if BLOCK in body:
                    text = text[:bs] + body.replace(BLOCK, "", 1) + text[be:]
                    total += 1
                    changed = True
                    break
            if not changed:
                break
        path.write_text(text, encoding="utf-8")

    # Verify
    remaining: list[str] = []
    for path in sorted(Path("supabase/migrations").glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        for m in HEAD.finditer(text):
            name = m.group("name")
            if name in KEEP_GUARD or not name.startswith(DERIVATION):
                continue
            bs = m.end()
            be = text.find("$$;", bs)
            if be != -1 and "is_trusted_context" in text[bs:be]:
                remaining.append(f"{path.name}: {name}")

    print(f"removed {total} misplaced guard(s)")
    if remaining:
        print("STILL GUARDED:")
        for r in remaining:
            print(f"  {r}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
