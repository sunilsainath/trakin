# MyTrakin — Operations

## Local

Docker is optional. Without it: API with env loaded from repo-root `.env`
(`python -m app.serve --port 8000` from `apps/api`), web with
`apps/web/.env.local` (`npm run dev`). Health: `/api/v1/health` (no auth),
`/api/v1/ready` (database + redis states). With Docker:
`docker compose up -d postgres redis` then `make db-reset`.

## Migrations

Forward-only files in `supabase/migrations`, ledgered in
`platform.schema_migrations`. `scripts/apply_migrations.py` (add `--reset`
only for empty/local rebuilds — never against shared data),
`scripts/verify_schema.py` (81 checks), `check_permission_resolution.py`.
CI reapplies from empty on every run.

## Background work

Celery + Redis (`app/workers/`): outbox dispatch, bank sync, invoice
generation, overdue marking, schedule advancement, expiry scans, document
intake, idempotency purge. Every task idempotent (at-least-once delivery).
Without Redis, tasks simply do not run — the API stays up; `/ready` reports
redis degraded.

## Observability & SLOs

Structured logs with request ids (`structlog`), Sentry (backend), health
probes. Targets: p50 < 100ms, p95 < 300ms, p99 < 500ms for normal APIs; OCR/AI/
sync/report work is async-only, never in-request.

## Runbooks

- 5xx with request id: correlate via logs; the id is on the response by design.
- Pooler `DuplicatePreparedStatement`: ensure `prepare_threshold=None`
  (engine default; tests/scripts set it explicitly).
- Locked-sheet teardown: TRUNCATE timesheet tables; never DELETE locked rows.
- RLS suite wipes fixture users/companies it creates — run it only against
  dev-tier data.
