"""Delete leftover test rows in foreign-key dependency order.

Several tables use ON DELETE RESTRICT deliberately (contracts, invoices, payments
are legal/financial artefacts), so a single `DELETE FROM companies` is refused.
This walks the graph explicitly.

Usage:
    python scripts/cleanup_test_data.py
"""

from __future__ import annotations

import os

import psycopg

DSN = os.environ.get("DATABASE_ADMIN_URL") or os.environ["DATABASE_URL"]

# Order matters: every table must be emptied before the one it references.
CHILD_FIRST = [
    "public.payment_allocations",
    "public.payment_matches",
    "public.payment_requests",
    "public.payment_schedules",
    "public.payments",
    "public.invoice_approvals",
    "public.invoice_allocations",
    "public.invoice_items",
    "public.invoices",
    "public.billing_runs",
    "public.timesheet_approvals",
    "public.timesheet_revisions",
    "public.timesheet_entries",
    "public.timesheets",
    "public.assignments",
    "public.leave_requests",
    "public.leave_balances",
    "public.leave_policies",
    "public.contract_approval_steps",
    "public.contract_line_items",
    "public.contract_roles",
    "public.contract_parties",
    "public.contracts",
    "public.sow_roles",
    "public.sows",
    "public.project_roles",
    "public.projects",
    "public.msa_requests",
    "public.msa_versions",
    "public.msas",
    "public.document_access_log",
    "public.document_versions",
    "public.documents",
    "public.ai_automation_runs",
    "public.ai_automations",
    "public.ai_actions",
    "public.ai_extractions",
    "public.ai_document_chunks",
    "public.ai_knowledge_documents",
    "public.conversation_members",
    "public.messages",
    "public.conversations",
    "public.post_shares",
    "public.post_reactions",
    "public.post_comments",
    "public.posts",
    "public.connection_requests",
    "public.connections",
    "public.user_blocks",
    "public.company_invitations",
    "public.role_permissions",
    "public.company_memberships",
    "public.company_roles",
    "public.companies",
    "public.user_skills",
    "public.user_educations",
    "public.user_experiences",
    "public.user_certifications",
    "public.user_privacy",
    "public.user_sensitive",
    "public.user_profiles",
    "public.user_security_flags",
]

USER_CHILD_FIRST = [
    "public.user_skills",
    "public.user_educations",
    "public.user_experiences",
    "public.user_certifications",
    "public.user_privacy",
    "public.user_sensitive",
    "public.user_profiles",
    "public.user_security_flags",
    "public.company_memberships",
    "public.assignments",
    "public.conversation_members",
    "public.posts",
    "public.connections",
    "public.connection_requests",
    "public.user_blocks",
    "public.timesheets",
    "public.leave_requests",
    "public.leave_balances",
    "public.ai_conversations",
    "public.ai_extractions",
    "public.ai_usage_events",
    "platform.notifications",
    "platform.user_sessions",
]

COUNTS = """
SELECT
  (SELECT count(*) FROM public.users
    WHERE email LIKE '%@rls.test' OR email LIKE '%@t.test') AS test_users,
  (SELECT count(*) FROM public.companies)                     AS companies,
  (SELECT count(*) FROM public.company_roles)                 AS roles,
  (SELECT count(*) FROM public.projects)                      AS projects,
  (SELECT count(*) FROM public.contracts)                     AS contracts,
  (SELECT count(*) FROM public.invoices)                      AS invoices,
  (SELECT count(*) FROM public.bank_transactions)             AS bank_txns
"""


def main() -> None:
    with psycopg.connect(DSN, autocommit=True) as conn:
        # Reset the append-only ledgers FIRST. Deleting a bank_account cascades to
        # bank_transactions, whose BEFORE DELETE trigger refuses the cascade, so
        # TRUNCATE has to run before the graph walk.
        conn.execute("TRUNCATE public.bank_transactions CASCADE")
        conn.execute("TRUNCATE platform.audit_logs CASCADE")

        for table in CHILD_FIRST:
            conn.execute(f"DELETE FROM {table}")  # noqa: S608 - fixed list above
        for table in USER_CHILD_FIRST:
            conn.execute(f"DELETE FROM {table}")  # noqa: S608
        conn.execute(
            "DELETE FROM public.users WHERE email LIKE '%%@rls.test' "
            "OR email LIKE '%%@t.test'"
        )
        conn.execute("DELETE FROM platform.outbox_events")
        conn.execute("DELETE FROM platform.idempotency_keys")

        with conn.cursor() as cur:
            cur.execute(COUNTS)
            for name, value in zip([d.name for d in cur.description or []], cur.fetchone() or ()):
                print(f"  {name:12} = {value}")


if __name__ == "__main__":
    main()
