# MyTrakin — Testing

Pyramid: unit (`test_unit.py`, `test_billing_unit.py`, no DB) → integration
(`test_integration_flow.py`, `test_api_domain.py`, `test_id_generation.py`,
`test_social_messaging.py`, `test_document_pipeline.py`, `test_ai_platform.py`)
→ security (`test_rls_security.py`, marker `security`, needs
`DATABASE_ADMIN_URL`). Web: vitest suites + `tsc` + eslint + production build.

## Rules

- Fixtures are the only writers: session `tenants` + per-test rolled-back
  `conn`; `bank_transactions` only inside rolled-back transactions.
- Non-BYPASSRLS impersonation (`mytrakin_api`) for anything asserting
  enforcement; `prepare_threshold=None` behind the pooler.
- Defect pins: a test may xfail(strict) naming the defect, or an endpoint may
  sit in `EXPECTED_SERVER_ERRORS` — both fail loudly the moment the defect is
  fixed, so pins rot into progress instead of silently persisting.
- Current state: **API suites green with zero pins** (192 + 10 + 3 + 5 tests);
  RLS 21/21; web 63 + lint + typecheck + build.

## Security matrix

T1 IDOR/BOLA · T2 membership≠access · T3 granular gating · T4 no escalation ·
T5 immutable ids · T6 contract machine · T7 allocation caps · T8/T9 timesheet
rules · T10 MSA gate · T11 derived totals · T12 AI filtering · T13 action
approval · T14 payment auth · T15/T19 append-only · T16 anon invisibility ·
T17 hidden columns · T18 last admin · T20 document gates · T21 full permission
set. Plus: manipulation attempts (totals, locked sheets, self-approval),
webhook forgery/duplicates, quarantine refusal, MFA-less login sanity.
