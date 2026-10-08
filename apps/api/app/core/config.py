"""Application settings.

Every setting is read from the environment. The only values allowed in a
`NEXT_PUBLIC_*` variable are the ones listed in `docs/environment.md`; this file
is server-side and must never be imported by frontend code.
"""

from __future__ import annotations

import functools
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "development", "staging", "production"]


class Settings(BaseSettings):
    """Typed, validated configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------------------------------------------------------- core
    environment: Environment = "development"
    debug: bool = False
    platform_name: str = "MyTrakin"
    api_host: str = "0.0.0.0"  # noqa: S104 - container entrypoint
    api_port: int = 8000
    api_workers: int = 4

    # Exact origins, never "*". A wildcard would defeat cookie-based CSRF
    # protection and leak the session to any site that can issue a request.
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # ------------------------------------------------------------ database
    # Non-superuser role. RLS is enforced for this connection.
    database_url: str
    # Optional read replica for reporting and search.
    database_read_replica_url: str | None = None
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout_s: int = 10
    db_statement_timeout_s: int = 15
    # The Supabase transaction pooler multiplexes one backend across sessions, so
    # session-level state (SET, prepared statements, advisory locks) is unsafe.
    db_pooled: bool = True

    # ------------------------------------------------------------- supabase
    supabase_url: str = ""
    # anon key: RLS-protected, safe in the browser bundle.
    supabase_anon_key: SecretStr = SecretStr("")
    # service role: SERVER ONLY. Bypasses RLS. Never returned to a client.
    supabase_service_role_key: SecretStr = SecretStr("")
    supabase_jwt_secret: SecretStr = SecretStr("")
    supabase_project_ref: str = ""

    # ---------------------------------------------------------------- redis
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # ------------------------------------------------------------------ auth
    require_email_verification: bool = True
    session_max_age_seconds: int = 60 * 60 * 24 * 30
    allow_google_login: bool = True
    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")

    # -------------------------------------------------------------- storage
    storage_bucket_documents: str = "documents"
    storage_bucket_uploads: str = "uploads"
    signed_url_ttl_seconds: int = 300
    max_upload_bytes: int = 25 * 1024 * 1024

    # ----------------------------------------------------------------- mail
    email_provider: str = "resend"
    email_from_address: str = "no-reply@mytrakin.app"
    email_from_name: str = "MyTrakin"
    resend_api_key: SecretStr = SecretStr("")

    # ------------------------------------------------------------------- ai
    ai_gateway_enabled: bool = True
    ai_default_chat_provider: str = "openai"
    ai_default_embedding_provider: str = "openai"
    ai_fallback_provider: str = "anthropic"
    openai_api_key: SecretStr = SecretStr("")
    openai_chat_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    anthropic_api_key: SecretStr = SecretStr("")
    anthropic_chat_model: str = "claude-3-5-sonnet-latest"
    gemini_api_key: SecretStr = SecretStr("")
    gemini_chat_model: str = "gemini-1.5-pro"
    local_llm_base_url: str = ""
    local_llm_api_key: SecretStr = SecretStr("")
    ai_max_context_chars: int = 120_000
    ai_citations_required: bool = True
    ai_pii_redaction: bool = True
    ai_daily_token_budget_per_user: int = 200_000
    ai_daily_cost_budget_cents_per_company: int = 5_000

    # ------------------------------------------------------------- payments
    # Plaid connects bank accounts; it does not move money.
    plaid_client_id: str = ""
    plaid_secret: SecretStr = SecretStr("")
    plaid_env: Literal["sandbox", "development", "production"] = "sandbox"
    plaid_webhook_url: str = ""
    # Fernet key material for envelope-encrypting access tokens at rest.
    plaid_token_encryption_key: SecretStr = SecretStr("")

    payment_processor: str = "stripe"
    payment_processor_api_key: SecretStr = SecretStr("")
    payment_processor_webhook_secret: SecretStr = SecretStr("")

    # ---------------------------------------------------------- observability
    log_level: str = "INFO"
    log_json: bool = True
    sentry_dsn: SecretStr = SecretStr("")
    sentry_environment: str = "development"
    sentry_traces_sample_rate: float = 0.1
    otel_exporter_otlp_endpoint: str = ""

    # ------------------------------------------------------------- rate limits
    # Per-subject, per-window. Different operations carry different limits because
    # their cost and abuse profile differ; see app/core/rate_limit.py.
    rate_limit_enabled: bool = True
    rate_limit_login: str = "10/15m"
    rate_limit_signup: str = "5/1h"
    rate_limit_password_reset: str = "5/1h"
    rate_limit_messaging: str = "60/1m"
    rate_limit_ai: str = "30/1m"
    rate_limit_search: str = "120/1m"
    rate_limit_upload: str = "20/1h"
    rate_limit_payments: str = "30/1m"
    rate_limit_bank_sync: str = "10/1m"
    rate_limit_default: str = "300/1m"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        """Accept both a JSON array and a comma-separated string."""
        if isinstance(v, str) and not v.strip().startswith("["):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @model_validator(mode="after")
    def _no_wildcard_cors(self) -> Settings:
        """A wildcard origin would defeat cookie CSRF protection and leak the
        session to any site able to issue a request. Reject it everywhere."""
        if any(o.strip() == "*" for o in self.cors_origins):
            raise ValueError("cors_origins must list explicit origins; '*' is not permitted")
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def async_database_url(self) -> str:
        """Normalise any libpq URL to the SQLAlchemy async driver form."""
        url = self.database_url
        for prefix in ("postgresql+psycopg://", "postgresql+psycopg_async://"):
            if url.startswith(prefix):
                return url
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+psycopg://", 1)
        if url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql+psycopg://", 1)
        return url

    def integration_ready(self, name: str) -> bool:
        """True when an external integration has the credentials it needs.

        A missing credential disables the feature with a typed
        `IntegrationNotConfigured` error. It never produces a fake success.
        """
        checks: dict[str, bool] = {
            "openai": bool(self.openai_api_key.get_secret_value()),
            "anthropic": bool(self.anthropic_api_key.get_secret_value()),
            "gemini": bool(self.gemini_api_key.get_secret_value()),
            "local_llm": bool(self.local_llm_base_url),
            "plaid": bool(self.plaid_client_id and self.plaid_secret.get_secret_value()),
            "payments": bool(self.payment_processor_api_key.get_secret_value()),
            "email": bool(self.resend_api_key.get_secret_value()),
            "sentry": bool(self.sentry_dsn.get_secret_value()),
        }
        return checks.get(name, False)


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings. Tests call `get_settings.cache_clear()` after patching env."""
    return Settings()
