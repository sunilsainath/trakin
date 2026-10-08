# MyTrakin — Unified Professional & Business Platform

Production-grade, multi-tenant SaaS combining professional networking, workforce management,
contracting (CORE), finance (billing + PAYMENTS) and an AI platform — all behind one
authorization model and PostgreSQL Row Level Security.

> **Status: implemented.** Multi-tenant RBAC + RLS, authentication (email verification,
> sessions, MFA, Google OAuth), contracting (CORE), timesheets + leave, billing + invoicing,
> payments + reconciliation, documents + MSA, social + messaging, global search and an AI
> platform (gateway, RAG, agents) — with unit, integration, RLS security and API-sweep
> suites, and CI running lint, types, unit tests, the web build and migrations-from-empty.
> See [`docs/validation-report.md`](docs/validation-report.md) for the acceptance record.

## Repository layout

```text
mytrakin/
├── apps/
│   ├── api/                 FastAPI service (auth, RBAC, domain logic, AI gateway, workers)
│   │   ├── app/
│   │   │   ├── api/         versioned routers (/api/v1)
│   │   │   ├── core/        config, security, logging, errors, rate limiting, idempotency
│   │   │   ├── db/          async engine, session identity (SET LOCAL), repositories
│   │   │   ├── schemas/     Pydantic v2 request/response contracts
│   │   │   ├── services/    domain services (business rules live here)
│   │   │   ├── ai/          AI gateway, providers, RAG, agents
│   │   │   ├── integrations/ Plaid, payments, email, OCR, storage adapters
│   │   │   └── workers/     Celery app and tasks
│   │   ├── alembic/         env scaffolding (supabase/migrations is authoritative)
│   │   └── tests/           unit, integration, permission and RLS security tests
│   └── web/                 Next.js App Router + TypeScript + Tailwind + shadcn/ui
├── supabase/
│   └── migrations/          authoritative schema + RLS policies (SQL)
├── docs/                    architecture, database, api, auth, rls, ai, security, ops
├── infra/                   Azure Container Apps notes + Dockerfiles for api/web
└── .github/workflows/       CI, security, CD
```

## Prerequisites

| Tool | Version | Notes |
| --- | --- | --- |
| Node.js | ≥ 20 | `node -v` |
| Python | ≥ 3.11 | `python -V` |
| Docker | any recent | runs local Postgres 16 + Redis |
| Supabase CLI | ≥ 1.200 | `supabase --version` |

## Quick start (local, no cloud account)

```bash
cp .env.example .env

# 1. Start Postgres 16 + Redis
docker compose up -d postgres redis

# 2. Apply schema + RLS policies (authoritative source)
make db-reset

# 3. API
cd apps/api
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
uvicorn app.main:app --reload

# 4. Web
cd apps/web
npm install
npm run dev
```

API docs: <http://localhost:8000/docs> · ReDoc: <http://localhost:8000/redoc>

### Using a real Postgres / Supabase project

Set `DATABASE_URL` (and `DATABASE_ADMIN_URL` for schema changes) in `.env`, then:

```bash
make db-apply   # apply pending migrations from supabase/migrations
make db-verify  # assert the live database has every object the app depends on
```

Set `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` and `DATABASE_URL` in `.env`.
The service-role key is **server-only**: it must never appear in `apps/web` or any `NEXT_PUBLIC_*`.

## Core commands

```bash
make help            # list all targets
make db-reset        # drop + rebuild schema from supabase/migrations
make test            # unit suites for both applications
make test-security   # RLS / permission / IDOR suite
make lint            # ruff + eslint
make typecheck       # mypy + tsc
make check           # full local gate, no database required
make api-dev         # run the FastAPI service with reload
make web-dev         # run the Next.js dev server
```

## Documentation

| Document | Contents |
| --- | --- |
| [architecture.md](docs/architecture.md) | topology, module map, identity pipeline, events, scale plan |
| [database.md](docs/database.md) | schema walkthrough, invariants, indexes, migrations policy |
| [authorization.md](docs/authorization.md) | RBAC, permission catalogue, segregation of duties |
| [rls.md](docs/rls.md) | policy catalogue, session identity, tenant-isolation tests |
| [api.md](docs/api.md) | conventions, pagination, idempotency, error envelope |
| [authentication.md](docs/authentication.md) | signup, verification, sessions, MFA, Google OAuth |
| [ai-architecture.md](docs/ai-architecture.md) | gateway, routing, budget, RAG, agents, safety |
| [security.md](docs/security.md) | threat model, controls, secret handling, reporting |
| [environment.md](docs/environment.md) | every variable, and which tier may read it |
| [testing.md](docs/testing.md) | test pyramid, security test matrix |
| [operations.md](docs/operations.md) | deployment, observability, SLOs, runbooks |
| [validation-report.md](docs/validation-report.md) | Phase 1 acceptance results |

## Security reporting

Do not open a public issue for a vulnerability. Follow [`docs/security.md`](docs/security.md) §Reporting.
