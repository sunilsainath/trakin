# MyTrakin API image.
# Build from the repository root: docker build -f infra/docker/api.Dockerfile .
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/api
COPY apps/api/pyproject.toml ./
RUN pip install --upgrade pip && pip install -e .[dev] --no-deps 2>/dev/null || pip install -e . ; \
    pip install fastapi "uvicorn[standard]" pydantic pydantic-settings email-validator \
      "sqlalchemy[asyncio]" "psycopg[binary,pool]" alembic asyncpg redis "celery[redis]" \
      httpx structlog "pyjwt[crypto]" cryptography argon2-cffi "sentry-sdk[fastapi,celery,sqlalchemy]" \
      python-multipart tenacity orjson supabase pytest pytest-asyncio

COPY apps/api/app ./app
COPY apps/api/alembic ./alembic
COPY apps/api/alembic.ini ./alembic.ini

# Migrations run as a separate job (see .github/workflows/cd.yml), never at
# container start: two replicas racing DDL is how you corrupt a schema.
EXPOSE 8000
CMD ["python", "-m", "app.serve", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
