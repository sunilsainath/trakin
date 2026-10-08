# MyTrakin on Azure (reference topology)

Traffic: Azure Front Door (WAF + CDN) → Azure Container Apps:

- `mytrakin-api`: `infra/docker/api.Dockerfile`, 2+ replicas, `/api/v1/ready`
  as the readiness probe, `/api/v1/health` as liveness. Secrets
  (`DATABASE_URL`, Supabase service key, AI/Plaid/processor keys) from Azure
  Key Vault via managed identity — never baked into images.
- `mytrakin-web`: `infra/docker/web.Dockerfile`, standalone output, Front Door
  caching static assets only (`/_next/static/*`), never `/api/*`.
- `mytrakin-worker`: same API image, `celery -A app.workers.celery_app worker`
  on the `critical,default,scheduled,bulk` queues.
- `migrator` job (run-to-completion on every deploy, before traffic shifts):
  `python scripts/apply_migrations.py` — migrations never run at container
  start.

Data plane stays on Supabase (Postgres + Auth + Storage + pgvector) with the
pooler in transaction mode; Redis is Azure Cache for Redis. Observability:
Sentry (already wired) plus Azure Monitor via the OpenTelemetry endpoint
(`OTEL_EXPORTER_OTLP_ENDPOINT`).

See `.github/workflows/cd.yml` for the pipeline that builds these images.
