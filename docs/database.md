# MyTrakin — Database

Authoritative schema: `supabase/migrations/*.sql` (forward-only, ledgered in
`platform.schema_migrations`). Apply with `make db-apply`, verify with
`make db-verify` (`scripts/verify_schema.py`, 81 checks).

## Identity

- Internal keys are UUIDv7 (`app.uuid7()`); external keys are immutable
  `public_id`s allocated by `app.gen_public_id(prefix, 8)` triggers and frozen
  by `app.freeze_public_id` (see `test_id_generation.py`).
- Prefixes: users `U`, companies `CO`, projects `P`, project roles `R`, SOWs
  `S`, contracts `C`, invoices `I`, payments `PM`, timesheets `TS`,
  leave requests `LR`, policies `LP`, bank accounts `BA`, documents `D`,
  MSAs `M`, schedules `PS`.
- `public.users` mirrors Supabase Auth (`auth_id`). New rows start
  `PENDING_VERIFICATION`; `POST /auth/bootstrap` flips confirmed users to
  `ACTIVE`. `ck_user_email_verified_status` enforces ACTIVE ⇒ verified.

## Commercial chain

`companies → projects → project_roles → sows → sow_roles → contracts →
contract_roles → assignments → timesheets → timesheet_entries → invoices →
invoice_items → payments → payment_allocations → bank_transactions`.

Key guards (all in SQL so the API cannot bypass them):

- Role capacity: allocation trigger refuses `allocated_count > required_count`.
- No orphan work: assignments/timesheets require an ACTIVE contract.
- Derived money: `assert_invoice` recomputes invoice totals from items;
  client-supplied totals are discarded. Amounts are `NUMERIC`, never float.
- Completed payments require `processor_payment_ref` or a bank link
  (`ck_payment_ref`); manual payments are labelled `offline:<key>`.
- Timesheet entries are refused on APPROVED/LOCKED sheets
  (`assert_timesheet_editable` reads `locked_at`); approved sheets change only
  via revisions.
- Invoices need an active MSA to leave DRAFT (`MSA_REQUIRED` flag otherwise).
- `platform.audit_logs` and `bank_transactions` are append-only (trigger
  rejects UPDATE/DELETE); timesheet teardown uses TRUNCATE for the same reason.

## RBAC tables

`companies → company_memberships → company_roles → role_permissions →
permissions`, plus `role_templates` copied per company by
`app.bootstrap_company_roles`. SUPER_ADMIN's template is
`ARRAY(SELECT key FROM permissions)`, so new keys flow to it automatically.
`app.my_permissions` / `app.has_permission` back both the API and RLS.

## AI tables

`ai_knowledge_documents` + `ai_document_chunks` (pgvector) with
`required_permission` per document; `app.ai_visible_chunks` filters by
permission *before* retrieval. `ai_actions` carries capability tokens and the
trigger refuses EXECUTED without approval (and a different approver for
CRITICAL). `ai_usage_events` meters tokens/cost with `was_cached`.

## Indexing

Partial/composite/GIN indexes on hot paths (company + status + dates, public_id
lookups, full-text vectors). Pagination is keyset-based (`schemas/common.py`).
No `SELECT *` in service reads.
