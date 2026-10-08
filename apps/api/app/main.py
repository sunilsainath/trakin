"""FastAPI application factory.

Middleware order matters and is deliberate:

  RequestContext  outermost, so every log line and error carries a request id
  Sentry         captures exceptions with that context
  CORSMiddleware explicit origins only
  TrustedHost    rejects unexpected Host headers
  GZip           compresses responses
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.status import HTTP_500_INTERNAL_SERVER_ERROR

# Must precede the database import: psycopg's async driver needs a
# selector-based loop, which is not the Windows default.
from app.core.event_loop import ensure_compatible_event_loop

ensure_compatible_event_loop()

from app.api.v1 import api_router  # noqa: E402
from app.core.config import Settings, get_settings  # noqa: E402
from app.core.errors import AppError  # noqa: E402
from app.core.logging import (  # noqa: E402
    bind_request_context,
    configure_logging,
    get_logger,
    new_request_id,
)
from app.db.session import dispose_engine  # noqa: E402

logger = get_logger(__name__)

DESCRIPTION = """
MyTrakin platform API.

**Authentication** — Supabase Auth issues the access token; the API verifies it
against Supabase's signing keys and derives identity from the verified claims.

**Authorization** — every company-scoped request carries `X-Company-Public-Id`.
The API resolves it through the membership table, then applies both
application-level RBAC (`require_permission`) and PostgreSQL Row Level Security.
Neither is trusted on its own: the API for actions, the database for rows.

**Money** — all amounts are exact decimals with an ISO-4217 currency. Amounts are
derived server-side and in the database; a client-supplied total is discarded.
"""

TAGS_METADATA: list[dict[str, Any]] = [
    {"name": "health", "description": "Liveness and readiness probes."},
    {"name": "auth", "description": "Session lifecycle and device management."},
    {"name": "identity", "description": "Profile, privacy and connections."},
    {"name": "business", "description": "Companies, memberships, roles, settings."},
    {"name": "search", "description": "Permission-aware global search."},
    {"name": "ai", "description": "Assistant, agents and AI governance."},
]


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign a request id, bind correlation context, and time the request."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Any]]
    ) -> Any:
        # Honour an upstream id (Azure Front Door) so a trace survives the hop.
        request_id = request.headers.get("x-request-id") or new_request_id()
        request.state.request_id = request_id

        started = time.perf_counter()
        with bind_request_context(request_id):
            try:
                response = await call_next(request)
            except Exception:
                duration_ms = int((time.perf_counter() - started) * 1000)
                logger.exception(
                    "request_failed",
                    method=request.method,
                    path=request.url.path,
                    duration_ms=duration_ms,
                )
                # A stack trace never reaches the client; the request id does, and
                # it has to travel on this response too: the 500 is the one reply a
                # support engineer must be able to correlate with the server log.
                return JSONResponse(
                    status_code=HTTP_500_INTERNAL_SERVER_ERROR,
                    content={
                        "error": {
                            "code": "INTERNAL_ERROR",
                            "message": "An unexpected error occurred.",
                            "request_id": request_id,
                        }
                    },
                    headers={
                        "X-Request-Id": request_id,
                        "X-Response-Time-ms": str(duration_ms),
                        "Cache-Control": "no-store",
                    },
                )

        duration_ms = int((time.perf_counter() - started) * 1000)
        response.headers["X-Request-Id"] = request_id
        response.headers["X-Response-Time-ms"] = str(duration_ms)
        # Defence in depth against caching an authenticated response.
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")

        logger.info(
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=duration_ms,
        )
        return response


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    if settings.sentry_dsn.get_secret_value():
        try:
            import sentry_sdk

            sentry_sdk.init(
                dsn=settings.sentry_dsn.get_secret_value(),
                environment=settings.sentry_environment,
                traces_sample_rate=settings.sentry_traces_sample_rate,
                send_default_pii=False,  # never attach user data automatically
            )
            logger.info("sentry_initialised")
        except Exception as exc:  # noqa: BLE001
            logger.warning("sentry_init_failed", error=str(exc)[:200])

    logger.info(
        "api_starting",
        environment=settings.environment,
        ai_gateway=settings.ai_gateway_enabled,
    )
    try:
        yield
    finally:
        await dispose_engine()
        logger.info("api_stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    app = FastAPI(
        title=f"{settings.platform_name} API",
        description=DESCRIPTION,
        version="1.0.0",
        openapi_tags=TAGS_METADATA,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # Middleware is applied bottom-up, so this list is outermost-first.
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=_allowed_hosts(settings),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Company-Public-Id",
            "X-Request-Id",
            "Idempotency-Key",
        ],
        expose_headers=["X-Request-Id", "X-Response-Time-ms"],
        max_age=600,
    )
    app.add_middleware(RequestContextMiddleware)

    _install_exception_handlers(app)
    app.include_router(api_router)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "service": settings.platform_name,
            "docs": "/docs",
            "health": "/api/v1/health",
        }

    return app


def _allowed_hosts(settings: Settings) -> list[str]:
    if settings.environment in {"local", "development"}:
        return ["*"]
    # In deployed environments the Host header must match the configured origins.
    hosts: list[str] = []
    for origin in settings.cors_origins:
        host = origin.split("//")[-1].split(":")[0]
        if host:
            hosts.append(host)
    return hosts or ["*"]


def _install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None) or exc.request_id
        if exc.status_code >= 500:
            logger.error("app_error", code=exc.code, message=exc.message)
        else:
            logger.info("app_error", code=exc.code, status=exc.status_code)

        payload = exc.to_payload()
        payload["error"]["request_id"] = request_id
        return JSONResponse(status_code=exc.status_code, content=payload)

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "The request contains invalid data.",
                    "request_id": request_id,
                    "details": {
                        "errors": [
                            {
                                "field": ".".join(str(p) for p in e.get("loc", [])[1:]),
                                "message": e.get("msg", ""),
                                "type": e.get("type", ""),
                            }
                            for e in exc.errors()[:20]
                        ]
                    },
                }
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        logger.exception("unhandled_exception", path=request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "An unexpected error occurred.",
                    "request_id": request_id,
                }
            },
        )


app = create_app()
