SHELL := /bin/bash
.DEFAULT_GOAL := help
COMPOSE := docker compose

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------- infrastructure
.PHONY: up
up: ## Start local Postgres + Redis
	$(COMPOSE) up -d postgres redis

.PHONY: down
down: ## Stop local services
	$(COMPOSE) down

.PHONY: logs
logs: ## Tail local service logs
	$(COMPOSE) logs -f --tail=100

# ---------------------------------------------------------------- database
.PHONY: db-reset
db-reset: ## Drop and rebuild the local database from supabase/migrations
	python scripts/apply_migrations.py --reset

.PHONY: db-apply
db-apply: ## Apply pending migrations from supabase/migrations
	python scripts/apply_migrations.py

.PHONY: db-verify
db-verify: ## Assert the live database has every object the app depends on
	python scripts/verify_schema.py

.PHONY: db-permissions
db-permissions: ## Prove permission resolution returns the full set
	python scripts/check_permission_resolution.py

.PHONY: db-smoke
db-smoke: ## Start the API and run the live HTTP/RLS smoke checks
	python scripts/run_api_smoke.py

.PHONY: db-stats
db-stats: ## Table sizes
	psql "$(DATABASE_URL)" -c "SELECT relname, n_live_tup, pg_size_pretty(pg_total_relation_size(relid)) FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 25;"

# ---------------------------------------------------------------- api
.PHONY: api-install
api-install: ## Install API dependencies
	python -m pip install -e "apps/api[dev]"

.PHONY: api-dev
api-dev: ## Run FastAPI with reload (Windows-safe event loop)
	cd apps/api && python -m app.serve --reload

.PHONY: api-test
api-test: ## Run the API unit suite (no database required)
	cd apps/api && python -m pytest -q tests/test_unit.py

# ---------------------------------------------------------------- web
.PHONY: web-install
web-install: ## Install web dependencies
	cd apps/web && npm install

.PHONY: web-dev
web-dev: ## Run the Next.js dev server
	cd apps/web && npm run dev

.PHONY: web-build
web-build: ## Production build
	cd apps/web && npm run build

.PHONY: web-test
web-test: ## Run the web unit suite
	cd apps/web && npm test

# ---------------------------------------------------------------- quality
.PHONY: lint
lint: ## Lint everything
	cd apps/api && ruff check . && ruff format --check .
	cd apps/web && npm run lint

.PHONY: typecheck
typecheck: ## Type-check everything
	cd apps/api && mypy app
	cd apps/web && npm run typecheck

.PHONY: format
format: ## Auto-format
	cd apps/api && ruff format . && ruff check --fix .
	cd apps/web && npm run format

# ---------------------------------------------------------------- full gate
.PHONY: test
test: ## Unit suites for both applications
	cd apps/api && python -m pytest -q tests/test_unit.py
	cd apps/web && npm test

.PHONY: test-security
test-security: ## RLS / permission / IDOR suite against the real database
	python -m pytest apps/api/tests/test_rls_security.py -q -m security

.PHONY: check
check: lint typecheck test ## Full local gate, no database required
