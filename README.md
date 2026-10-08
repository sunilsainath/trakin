# MyTrakin — Unified Professional & Business Platform

Production-grade, multi-tenant SaaS combining professional networking, workforce management,
contracting (CODE), finance (billing + PAYMENTS) and an AI platform — all behind one
authorization model and PostgreSQL Row Level Security.

> **Status: Phase 1 — Foundation.** Schema, RLS, RBAC, authentication, design system, API
> foundation, CI/CD and documentation are implemented. Module verticals land in later phases
> per [`docs/architecture.md`](docs/architecture.md) §10. See [`docs/validation-report.md`](docs/validation-report.md).

## Repository layout

```text
mytrakin/
├── apps/
│   ├── api/                 FastAPI service (auth, RBAC, domain logic, AI gateway, workers)
│   │   ├── app/
│   │   │   ├── api/         versioned routers (/api/v1)
│   │   │   ├── core/        config, security, logging, errors, rate limiting, idempotency
│   │   │   ├── db/          async engine, session identity (SET LOCAL), repositories
│   │   │   ├── models/      SQLAlchemy ORM models mirroring migrations
│   │   │   ├── schemas/     Pydantic v2 request/response contracts
│   │   │   ├── services/    domain services (business rules live here)
│   │   │   ├── ai/          AI gateway, providers, RAG, agents
│   │   │   ├── integrations/ Plaid, payments, email, OCR, storage adapters
│   │   │   └── workers/     Celery app and tasks
│   │   ├── alembic/         forward-only migrations (mirrors supabase/migrations)
│   │   └── tests/           unit, integration, permission and RLS security tests
│   └── web/                 Next.js App Router + TypeScript + Tailwind + shadcn/ui
├── supabase/
│   ├── migrations/          authoritative schema + RLS policies (SQL)
│   ├── seed.sql             development-only reference data (permissions, roles)
│   └── config.toml
├── docs/                    architecture, database, api, auth, rls, ai, security, ops
├── infra/                   Azure Bicep / Terraform placeholders + container definitions
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

### Using a real Supabase project

```bash
supabase login
supabase link --project-ref "$SUPABASE_PROJECT_REF"
supabase db push          # applies supabase/migrations
supabase functions new verify-email   # see docs/authentication.md
```

Set `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` and `DATABASE_URL` in `.env`.
The service-role key is **server-only**: it must never appear in `apps/web` or any `NEXT_PUBLIC_*`.

## Core commands

```bash
make help            # list all targets
make db-reset        # drop + recreate local schema, apply migrations, load seed
make db-diff         # verify migrations match models (CI gate)
make test            # API test suite
make test-security   # RLS / permission / IDOR suite
make lint            # ruff + eslint
make typecheck       # mypy + tsc
make dev             # run API + web concurrently
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
