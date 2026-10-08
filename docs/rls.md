# MyTrakin — Row Level Security

Policies live in `supabase/migrations/0012_rls_policies.sql` (168 policies).
Session identity is set per transaction by `set_identity`
(`SET LOCAL app.user_id / app.company_id / app.request_id`), read by helpers
`app.current_*()` and `app.has_permission()` — the same function the API uses,
so the two layers cannot disagree.

## Test strategy (`apps/api/tests/test_rls_security.py`, marker `security`)

Tests impersonate the non-BYPASSRLS `mytrakin_api` role (the `DATABASE_URL`
role would bypass everything, proving nothing) with one connection per
impersonation (pooler-safe, `prepare_threshold=None`).

Matrix T1–T21: cross-company IDOR/BOLA, membership≠access, granular gating, no
escalation, immutable public ids, contract/allocation/timesheet/invoice/payment
machines, append-only ledgers, unauthenticated invisibility, hidden columns,
last-admin protection, tenant-gated documents/AI, full-set permission
resolution (the `array_agg` T21 trap). Run: `make test-security`.

## Teardown note

Locked timesheets refuse row DELETE by design, so the fixture truncates the
timesheet tables (TRUNCATE fires no row triggers) instead of fighting the guard
it just proved.
