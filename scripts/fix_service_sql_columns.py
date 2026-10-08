"""Repair the service-layer SQL that referenced columns the schema does not have.

Three families of defect, all found by the integration suite:

D1  `<company alias>.name`  -- public.companies has legal_name / display_name.
D7  `COALESCE(up.display_name, up.first_name)` -- public.user_profiles has no
    name columns at all; the name lives on public.users (first_name/last_name),
    which every one of these queries already joins.
D8  `a.created_at` on platform.audit_logs -- the event time column is occurred_at.

Run once, from the repository root:
    .venv\\Scripts\\python.exe scripts\\fix_service_sql_columns.py
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVICES = ROOT / "apps" / "api" / "app" / "services"

#: A person's display name, from the only table that carries one. Both name
#: columns are NOT NULL, so the concatenation cannot collapse to NULL; TRIM +
#: NULLIF keeps a nameless user NULL rather than a single space.
def person(alias: str) -> str:
    return f"NULLIF(TRIM({alias}.first_name || ' ' || {alias}.last_name), '')"


def company(alias: str) -> str:
    return f"COALESCE({alias}.display_name, {alias}.legal_name)"


# (file, [(pattern, replacement, is_regex, expected_count)])
PATCHES: list[tuple[str, list[tuple[str, str, bool, int]]]] = [
    (
        "ai_domain.py",
        [
            (r"cp\.name AS counterparty", company("cp") + " AS counterparty", True, 1),
            (
                r"COALESCE\(c\.name, 'n/a'\) AS counterparty",
                "COALESCE(c.display_name, c.legal_name, 'n/a') AS counterparty",
                True,
                1,
            ),
            (r"COALESCE\(up\.display_name, up\.first_name\)", person("u"), True, 2),
            (
                r"GROUP BY u\.public_id, up\.display_name, up\.first_name",
                "GROUP BY u.public_id, u.first_name, u.last_name",
                True,
                1,
            ),
            (r"[ \t]*LEFT JOIN public\.user_profiles up ON up\.user_id = [^\n]*\n", "", True, 2),
        ],
    ),
    (
        "code.py",
        [
            (
                r"op\.public_id AS owner_public_id,\n(\s*)COALESCE\(op\.display_name, op\.first_name\) AS owner_name,",
                "ou.public_id AS owner_public_id,\n" + "\\1" + person("ou") + " AS owner_name,",
                True,
                1,
            ),
            (
                r"LEFT JOIN public\.user_profiles op ON op\.user_id = p\.owner_user_id",
                "LEFT JOIN public.users ou ON ou.id = p.owner_user_id",
                True,
                1,
            ),
            (r"cp\.name AS client_name,", company("cp") + " AS client_name,", True, 1),
            (r"SELECT c\.public_id, c\.name\n", "SELECT c.public_id, " + company("c") + " AS name\n", True, 1),
            (
                r"cp\.public_id AS counterparty_company_public_id, cp\.name AS counterparty_company_name,",
                "cp.public_id AS counterparty_company_public_id,\n"
                "                   " + company("cp") + " AS counterparty_company_name,",
                True,
                1,
            ),
            (r"COALESCE\(cu\.display_name, cu\.first_name\)", person("cu"), True, 1),
            (r"COALESCE\(up\.display_name, up\.first_name\)", person("u"), True, 1),
            (r"[ \t]*LEFT JOIN public\.user_profiles up ON up\.user_id = [^\n]*\n", "", True, 1),
            (r"ORDER BY a\.created_at DESC", "ORDER BY a.occurred_at DESC", True, 1),
            (r"a\.created_at", "a.occurred_at AS created_at", True, 1),
        ],
    ),
    (
        "contracts.py",
        [
            (
                r"cp\.public_id AS counterparty_company_public_id, cp\.name AS counterparty_company_name,",
                "cp.public_id AS counterparty_company_public_id,\n"
                "           " + company("cp") + " AS counterparty_company_name,",
                True,
                1,
            ),
            (r"SELECT cp\.public_id AS company_id, cp\.name AS company_name,",
                "SELECT cp.public_id AS company_id, " + company("cp") + " AS company_name,", True, 1),
            (r"COALESCE\(cu\.display_name, cu\.first_name\)", person("cu"), True, 2),
            (r"COALESCE\(up\.display_name, up\.first_name\)", person("u"), True, 1),
            (r"[ \t]*LEFT JOIN public\.user_profiles up ON up\.user_id = [^\n]*\n", "", True, 1),
            (r"ORDER BY a\.created_at DESC", "ORDER BY a.occurred_at DESC", True, 1),
            (r"a\.created_at", "a.occurred_at AS created_at", True, 1),
        ],
    ),
    (
        "dashboard.py",
        [
            (r"COALESCE\(up\.display_name, up\.first_name\)", person("u"), True, 1),
            (r"[ \t]*LEFT JOIN public\.user_profiles up ON up\.user_id = [^\n]*\n", "", True, 1),
            (r"ORDER BY a\.created_at DESC", "ORDER BY a.occurred_at DESC", True, 1),
            (r"a\.created_at", "a.occurred_at AS created_at", True, 1),
        ],
    ),
    (
        "documents.py",
        [
            (r"COALESCE\(up\.display_name, up\.first_name\)", person("u"), True, 3),
            (r"[ \t]*LEFT JOIN public\.user_profiles up ON up\.user_id = [^\n]*\n", "", True, 3),
        ],
    ),
    (
        "invoicing.py",
        [
            (
                r"cp\.public_id AS counterparty_company_public_id, cp\.name AS counterparty_company_name,",
                "cp.public_id AS counterparty_company_public_id,\n"
                "           " + company("cp") + " AS counterparty_company_name,",
                True,
                1,
            ),
            (r"SELECT cp\.name AS counterparty,", "SELECT " + company("cp") + " AS counterparty,", True, 1),
            (r"GROUP BY cp\.name", "GROUP BY " + company("cp"), True, 1),
            (r"COALESCE\(cu\.display_name, cu\.first_name\)", person("cu"), True, 1),
            (r"COALESCE\(up\.display_name, up\.first_name\)", person("u"), True, 2),
            (r"[ \t]*LEFT JOIN public\.user_profiles up ON up\.user_id = [^\n]*\n", "", True, 2),
            (r"ORDER BY a\.created_at DESC", "ORDER BY a.occurred_at DESC", True, 1),
            (r"a\.created_at", "a.occurred_at AS created_at", True, 1),
        ],
    ),
    (
        "msas.py",
        [
            (
                r"ca\.public_id AS company_a_public_id, ca\.name AS company_a_name,",
                "ca.public_id AS company_a_public_id, " + company("ca") + " AS company_a_name,",
                True,
                1,
            ),
            (
                r"cb\.public_id AS company_b_public_id, cb\.name AS company_b_name,",
                "cb.public_id AS company_b_public_id, " + company("cb") + " AS company_b_name,",
                True,
                1,
            ),
            (
                r"rc\.public_id AS requester_company_id, rc\.name AS requester_company_name,",
                "rc.public_id AS requester_company_id,\n"
                "                           " + company("rc") + " AS requester_company_name,",
                True,
                1,
            ),
            (
                r"tc\.public_id AS target_company_id, tc\.name AS target_company_name",
                "tc.public_id AS target_company_id,\n"
                "                           " + company("tc") + " AS target_company_name",
                True,
                1,
            ),
        ],
    ),
    (
        "payments.py",
        [
            (r"COALESCE\(up\.display_name, up\.first_name\)", person("u"), True, 1),
            (r"[ \t]*LEFT JOIN public\.user_profiles up ON up\.user_id = [^\n]*\n", "", True, 1),
            (
                r"cp\.public_id AS counterparty_company_public_id, cp\.name AS counterparty_company_name,",
                "cp.public_id AS counterparty_company_public_id,\n"
                "           " + company("cp") + " AS counterparty_company_name,",
                True,
                1,
            ),
            (r"cp\.name AS counterparty,", company("cp") + " AS counterparty,", True, 2),
        ],
    ),
    (
        "work.py",
        [
            (r"COALESCE\(up\.display_name, up\.first_name\)", person("u"), True, 4),
            (r"[ \t]*LEFT JOIN public\.user_profiles up ON up\.user_id = [^\n]*\n", "", True, 4),
        ],
    ),
]


def main() -> int:
    failures = 0
    for name, patches in PATCHES:
        path = SERVICES / name
        text = path.read_text(encoding="utf-8")
        original = text
        for pattern, replacement, _is_regex, expected in patches:
            # m.expand resolves the `\1` group references used to keep the
            # surrounding indentation intact.
            text, count = re.subn(pattern, lambda m, r=replacement: m.expand(r), text)
            if count != expected:
                print(f"MISS  {name}: {pattern[:60]!r} matched {count}, expected {expected}")
                failures += 1
        if text != original:
            path.write_text(text, encoding="utf-8")
            print(f"patched {name}")
        leftover = re.findall(r"\b(?:up|op)\.[a-z_]+", text)
        if leftover:
            print(f"LEFTOVER {name}: {sorted(set(leftover))}")
            failures += 1
    print("failures:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
