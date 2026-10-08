# MyTrakin — Environment

Tiers: local / development / staging / production (`Settings.environment`).
Copy `.env.example` to `.env` (never committed). One Supabase project, one
database, one set of AI/Plaid/processor keys per tier — never share production
secrets with lower tiers.

## Browser-safe (`NEXT_PUBLIC_*`)

Only these reach the bundle: `NEXT_PUBLIC_API_BASE_URL`,
`NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`,
`NEXT_PUBLIC_APP_NAME`, `NEXT_PUBLIC_ENABLE_AI`. The anon key is
RLS-protected and safe to expose. Anything else here is a finding.

## Server-only (never in the browser)

`SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`, `DATABASE_URL`,
`DATABASE_ADMIN_URL`, `REDIS_URL`/Celery URLs, `RESEND_API_KEY`,
`OPENAI/ANTHROPIC/GEMINI_API_KEY`, `PLAID_*`, `PAYMENT_PROCESSOR_*`,
`PLAID_TOKEN_ENCRYPTION_KEY`, `SENTRY_DSN`, `MALWARE_SCANNER_URL` (host:port
only, no credentials).

## Local dev specifics

- API reads the repo-root `.env` from the process environment (pydantic
  `env_file=".env"` resolves relative to CWD, so launch with env loaded).
- Web needs `apps/web/.env.local` for the three `NEXT_PUBLIC_*` values (Next
  does not read the repo-root `.env`).
- `REQUIRE_EMAIL_VERIFICATION=false` locally: the shipped check is correct
  for prod (DB-backed), but Supabase issues no JWT claim for it.
- `DATABASE_URL` points at Supabase (pooler) in this workspace; the app
  disables server-side prepares (`prepare_threshold=None`) for pooler safety.
- Redis is optional locally: rate limiting degrades, `/ready` reports it.
