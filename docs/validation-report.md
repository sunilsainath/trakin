# MyTrakin — Validation report

## Scope

Phases 1–10 verified against the live Supabase project (pooler, Postgres 16 +
pgvector) on 2026-10-08/09: full API suites, RLS attack suite, web
lint/typecheck/unit/build, migration-from-empty, permission resolution.

## Results

- API: **192 passed** (`test_unit`, `test_billing_unit`, `test_id_generation`,
  `test_integration_flow`, `test_api_domain`) — zero xfails, zero pinned 500s.
- New suites: social/messaging/schedules/webhooks **10 passed**; document
  intake **3 passed**; AI platform **5 passed**.
- RLS/security: **21/21** as non-BYPASSRLS `mytrakin_api`.
- Web: eslint + `tsc` + **63 vitest** + production build (39 routes) green.
- Schema: `verify_schema.py` **81/81**; migrations 0001–0020 apply cleanly.
- Live probes: signup → verify → login (+MFA path) → bootstrap → workspace;
  posts, conversations, schedules, webhooks, notification preferences.

## Defects found and fixed in this cycle

0016 timesheet guard · 0017 metadata columns · 0018 social permissions ·
0019 mutual-count recursion · 0020 schedule allocator · lateral-alias selects ·
coroutine await · date arithmetic · payment ref CHECK · UUID serialization ·
mime/content columns · ai_insights columns · flag key · dict-safe agents ·
503 FEATURE_DISABLED · credit-note 422 · CSP placeholder · manifest format ·
`/users/me` path · pooler prepares · RLS teardown · email-verification
redesign · webhook/outbox binds · connection generated columns · reaction PK.

## Residual risks (tracked, not hidden)

- No load/E2E performance proof yet (targets in `operations.md`).
- Realtime channels not yet subscribed by the UI.
- OCR backends beyond plain text await keys; extraction stays honestly PENDING.
- Platform admin console is read-only overview (no user/company management UI).

## Business-module round (business branch)

End-to-end Business flow per the product flow diagram (landing → signup →
verify → login → feed → profile → Business → CODE → WORK → invoice →
payment), reusing the existing architecture — no duplicate tables, no second
auth system.

Defects found and fixed in this cycle:

- `company_invitations` had no `public_id` (0027): every invite failed with
  UndefinedColumn; the flow never worked end-to-end.
- `invite_member` audit passed a public id where a UUID was required.
- `create_role`/`update_role_permissions` `unnest(:keys)` ambiguous for
  single-element arrays → `CAST(:keys AS text[])`.
- SOW `REJECTED` status missing from the `sow_status` enum (0029): every
  rejection failed; fixed + lifecycle tests.
- Invoice submit ignored `msa_required`: No-MSA invoices could leave Draft.
  Submission now refuses with MSA_REQUIRED; UI hides Submit while blocked.
- Timesheet Return dialog discarded the typed reason; now sent as notes.
- `create_document` enum CASE needed explicit casts; `resolve_scoped`
  await-precedence bug (`await x["id"]`) broke every document upload.
- `timesheet_approval_chain` was never writable: added to the contract
  schemas/services with member-or-counterparty validation + detail-page
  editor; the submit trigger materialises the steps.
- `contract_roles.sow_role_id` was never written: SOW acceptance now
  generates one DRAFT contract per role, idempotent per role.

Added: invitation preview/accept endpoints + accept page; W-9 identity
(tax classification, TIN type + last-4, address) with field-level
validation, masked responses and a review step in onboarding and company
creation; company-post publishing (`posts.create`, actor vs publisher
identity); document detail page; timesheet CSV/XLSX import with
review-and-confirm; vendor bills (PAYABLE) with AR/AP toggle; profile
career history (education/experience/skills/visa); business notifications
(member/role/SOW/document) with preference respect; Playwright E2E
(10 specs green against a production build).

Deliberately not built: ads (no ad system exists; a placeholder would be
fake), remember-me checkbox (sessions already persist 30 days; a checkbox
would be fake), cross-party invoice inbox, "Active (to be paid)" as a
separate status (APPROVED covers it), billing-frequency period enforcement
(periods stay caller-chosen).
