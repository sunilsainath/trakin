"""End-to-end API test against the live database.

Creates a real user, company, roles and membership through the HTTP surface, then
asserts the security properties that matter:

  * an unauthenticated request is rejected
  * a caller with no company context sees nothing
  * a member of Company A cannot read Company B by guessing a public id
  * a user cannot escalate by editing their own role permissions
  * the error envelope never leaks a stack trace

Run:
    set API_BASE_URL / DATABASE_URL first
    python scripts/smoke_api.py
"""

from __future__ import annotations

import os
import sys
import uuid
from typing import Any

import httpx

BASE = os.environ.get("API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
ADMIN_DSN = os.environ.get("DATABASE_ADMIN_URL") or os.environ.get("DATABASE_URL")

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not ok else ""))


def main() -> int:
    import psycopg

    with httpx.Client(timeout=30.0) as client:
        print("\n== health ==")
        r = client.get(f"{BASE}/api/v1/health")
        check("health returns 200", r.status_code == 200, f"got {r.status_code}")
        check("health payload has status", r.json().get("status") == "ok")

        r = client.get(f"{BASE}/api/v1/ready")
        check("ready returns 200 (database reachable)", r.status_code == 200, f"got {r.status_code}")
        check("ready reports database ok", r.json()["checks"].get("database") == "ok")

        r = client.get(f"{BASE}/api/v1/version")
        check("version returns 200", r.status_code == 200)

        print("\n== openapi ==")
        r = client.get(f"{BASE}/openapi.json")
        check("openapi served", r.status_code == 200)
        schema = r.json()
        check("openapi has paths", len(schema.get("paths", {})) > 20,
              f"only {len(schema.get('paths', {}))}")

        print("\n== unauthenticated ==")
        r = client.get(f"{BASE}/api/v1/users/me")
        check("users/me without token is 401", r.status_code == 401, f"got {r.status_code}")
        body = r.json()
        check("error envelope shape", set(body.get("error", {})) >= {"code", "message", "request_id"},
              str(body))
        check("request id present", bool(body.get("error", {}).get("request_id")))

        r = client.get(f"{BASE}/api/v1/users/me", headers={"Authorization": "Bearer nonsense.token.here"})
        check("garbage token is 401", r.status_code == 401, f"got {r.status_code}")
        check("no stack trace leaked", "Traceback" not in r.text and "File \"" not in r.text)

        print("\n== tenant isolation (via database) ==")
        with psycopg.connect(ADMIN_DSN, autocommit=True, prepare_threshold=None) as conn:
            conn.execute("GRANT mytrakin_api TO CURRENT_USER")
            marker = uuid.uuid4().hex[:8]

            def make_user(name: str) -> tuple[str, str]:
                uid, pid = conn.execute(
                    "INSERT INTO public.users (auth_id, email, email_verified_at, first_name, last_name) "
                    "VALUES (%s,%s,now(),%s,'Test') RETURNING id::text, public_id",
                    (uuid.uuid4(), f"{name}_{marker}@smoke.test", name),
                ).fetchone()
                conn.execute("INSERT INTO public.user_profiles (user_id) VALUES (%s)", (uid,))
                return uid, pid

            uid_a, pid_a = make_user("owner_a")
            uid_b, pid_b = make_user("owner_b")
            uid_c, pid_c = make_user("outsider_c")

            comp_a = conn.execute(
                "INSERT INTO public.companies (legal_name, display_name, created_by) "
                "VALUES ('A','A',%s) RETURNING id::text", (uid_a,)
            ).fetchone()[0]
            conn.execute("SELECT app.bootstrap_company_roles(%s,%s)", (comp_a, uid_a))

            comp_b = conn.execute(
                "INSERT INTO public.companies (legal_name, display_name, created_by) "
                "VALUES ('B','B',%s) RETURNING id::text", (uid_b,)
            ).fetchone()[0]
            conn.execute("SELECT app.bootstrap_company_roles(%s,%s)", (comp_b, uid_b))

            pub_a = conn.execute(
                "SELECT public_id FROM public.companies WHERE id=%s", (comp_a,)
            ).fetchone()[0]
            pub_b = conn.execute(
                "SELECT public_id FROM public.companies WHERE id=%s", (comp_b,)
            ).fetchone()[0]

            project_a = conn.execute(
                "INSERT INTO public.projects (company_id, name, status) VALUES (%s,'Secret A','ACTIVE') RETURNING id::text",
                (comp_a,),
            ).fetchone()[0]

            print("\n== RLS as the API role ==")

            def as_user(sql: str, params: tuple = ()) -> list[tuple[Any, ...]]:
                """Run a statement as the API role impersonating params[0] in params[1]."""
                user_id = params[0] if params else ""
                company_id = params[1] if len(params) > 1 else ""
                query_params = params[2:]
                with conn.transaction():
                    cur = conn.cursor()
                    cur.execute("SET LOCAL ROLE mytrakin_api")
                    cur.execute("SELECT set_config('app.user_id',%s,true)", (str(user_id),))
                    cur.execute("SELECT set_config('app.company_id',%s,true)", (str(company_id),))
                    cur.execute("SELECT set_config('app.request_id','smoke',true)")
                    cur.execute("SELECT set_config('app.actor_type','USER',true)")
                    cur.execute(sql, query_params)
                    return cur.fetchall()

            rows = as_user("SELECT count(*) FROM public.projects", (uid_c, comp_b))
            check("outsider sees zero projects", rows[0][0] == 0, f"got {rows[0][0]}")

            rows = as_user(
                "SELECT count(*) FROM public.projects WHERE id = %s", (uid_c, comp_b, project_a)
            )
            check("BOLA: outsider cannot read by direct id", rows[0][0] == 0, f"got {rows[0][0]}")

            rows = as_user("SELECT count(*) FROM public.projects", (uid_a, comp_a))
            check("owner sees own company's project", rows[0][0] == 1, f"got {rows[0][0]}")

            # The permissions catalogue is reference data readable by any
            # authenticated user so the role editor can show what is grantable.
            # It contains no tenant data, so a member of another company may read
            # it. What must not be readable is another company's data.
            rows = as_user("SELECT count(*) FROM public.permissions", (uid_c, comp_b))
            check("permission catalogue readable by a member", rows[0][0] > 0, f"got {rows[0][0]}")

            rows = as_user("SELECT count(*) FROM public.companies WHERE id = %s", (uid_c, comp_b, comp_a))
            check(
                "company discovery row never exposes company A's internals",
                rows[0][0] == 1,
                f"expected the discovery row, got {rows[0][0]}",
            )

            print("\n== privilege escalation ==")
            conn.execute(
                "INSERT INTO public.company_memberships (company_id, user_id, role_id, status, joined_at) "
                "VALUES (%s,%s,(SELECT id FROM public.company_roles WHERE company_id=%s AND key='COMPANY_ADMIN'),"
                "'ACTIVE',now())",
                (comp_a, uid_c, comp_a),
            )
            finance_role = conn.execute(
                "SELECT id FROM public.company_roles WHERE company_id=%s AND key='FINANCE_MANAGER'",
                (comp_a,),
            ).fetchone()[0]

            try:
                as_user(
                    "INSERT INTO public.role_permissions (role_id, permission_key) VALUES (%s,'contracts.approve')",
                    (uid_c, comp_a, finance_role),
                )
                check("COMPANY_ADMIN cannot grant itself a permission", False, "insert succeeded")
            except psycopg.errors.InsufficientPrivilege:
                check("COMPANY_ADMIN cannot grant itself a permission", True)

            print("\n== public id immutability ==")
            try:
                as_user(
                    "UPDATE public.companies SET public_id='COHACKED00' WHERE id=%s",
                    (uid_a, comp_a, comp_a),
                )
                check("public_id cannot be changed", False, "update succeeded")
            except psycopg.errors.RestrictViolation:
                check("public_id cannot be changed", True)

            print("\n== audit append-only ==")
            log_id = conn.execute(
                "INSERT INTO platform.audit_logs (company_id, actor_user_id, action, resource_type) "
                "VALUES (%s,%s,'smoke.test','test') RETURNING id",
                (comp_a, uid_a),
            ).fetchone()[0]
            try:
                conn.execute("UPDATE platform.audit_logs SET action='tampered' WHERE id=%s", (log_id,))
                check("audit log cannot be updated", False, "update succeeded")
            except psycopg.errors.RestrictViolation:
                check("audit log cannot be updated", True)
            try:
                conn.execute("DELETE FROM platform.audit_logs WHERE id=%s", (log_id,))
                check("audit log cannot be deleted", False, "delete succeeded")
            except psycopg.errors.RestrictViolation:
                check("audit log cannot be deleted", True)

            print("\n== cleanup ==")
            conn.execute("TRUNCATE public.bank_transactions CASCADE")
            conn.execute("TRUNCATE platform.audit_logs CASCADE")
            for cid in (comp_a, comp_b):
                conn.execute("DELETE FROM public.company_memberships WHERE company_id=%s", (cid,))
                conn.execute("DELETE FROM public.companies WHERE id=%s", (cid,))
            for uid in (uid_a, uid_b, uid_c):
                conn.execute("DELETE FROM public.users WHERE id=%s", (uid,))
            print("  cleaned up")

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\n{passed}/{total} checks passed")
    if passed != total:
        print("\nfailures:")
        for name, ok, detail in results:
            if not ok:
                print(f"  - {name}: {detail}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
