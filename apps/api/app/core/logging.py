"""Structured logging with secret redaction and request correlation.

Rules enforced here:
  * never log a secret, token, or full identifier (TIN/SSN/bank/AI keys)
  * never log a full Authorization header
  * every log line carries the request id so a user-reported error is traceable
  * JSON in every deployed environment, human-readable locally
"""

from __future__ import annotations

import logging
import re
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import structlog

# --------------------------------------------------------------------------- context
request_id_var: ContextVar[str] = ContextVar("request_id", default="")
user_id_var: ContextVar[str] = ContextVar("user_id", default="")
company_id_var: ContextVar[str] = ContextVar("company_id", default="")

# Keys whose values are replaced wholesale, at any nesting depth.
SENSITIVE_KEYS = frozenset(
    {
        "password",
        "new_password",
        "current_password",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "api_key",
        "apikey",
        "secret",
        "client_secret",
        "service_role_key",
        "authorization",
        "cookie",
        "set-cookie",
        "ssn",
        "ssn_encrypted",
        "tax_id",
        "tax_id_encrypted",
        "tin",
        "bank_account_number",
        "routing_number",
        "card_number",
        "cvv",
        "account_number",
        "iban",
        "plaid_token",
        "item_id_encrypted",
        "access_token_encrypted",
        "encryption_key",
        "private_key",
    }
)

REDACTED = "[REDACTED]"

# Free-text patterns that can carry a secret even under an innocent key.
_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Bearer tokens and JWTs
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{20,}"),
    re.compile(r"\beyJ[A-Za-z0-9._\-]{20,}"),  # JWT
    # Provider key prefixes, anchored so a bare word is not matched.
    re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?i)\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)\bsk-ant-[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?i)\bAKIA[0-9A-Z]{16}\b"),  # AWS access key id
    # key=value, "key": "value" and 'key' => 'value' for sensitive names. The key
    # is captured so the log line stays searchable; only the value is replaced.
    # A bare `key` is included because it is a common way for a credential to
    # reach a log line, and the optional quote before the separator covers JSON.
    re.compile(
        r"(?i)\b(password|token|secret|api[_-]?key|authorization|key)\b"
        r"[\"']?\s*(?:=>|[:=])\s*"
        r"(?:\"[^\"]*\"|'[^']*'|[^\s,;&}\]]+)"
    ),
    # SSN and 9-digit TIN shaped values
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
)


def redact_text(value: str) -> str:
    """Remove secret-shaped substrings from free text."""

    def repl(m: re.Match[str]) -> str:
        # Group 1 exists only for the key=value pattern, whose key should be kept
        # so the line stays searchable. Every other pattern is replaced wholesale.
        if m.re.groups >= 1 and m.group(1) is not None:
            return f"{m.group(1)}={REDACTED}"
        return REDACTED

    out = value
    for pattern in _PATTERNS:
        out = pattern.sub(repl, out)
    return out


def scrub(value: Any, _depth: int = 0) -> Any:
    """Recursively redact sensitive values from a structure about to be logged."""
    if _depth > 12:  # guard against pathological nesting
        return "[TRUNCATED]"

    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, val in value.items():
            if isinstance(key, str) and key.lower() in SENSITIVE_KEYS:
                result[key] = REDACTED
            else:
                result[key] = scrub(val, _depth + 1)
        return result

    if isinstance(value, (list, tuple)):
        return [scrub(v, _depth + 1) for v in value]

    if isinstance(value, str):
        return redact_text(value)

    return value


# ------------------------------------------------------------------- processors
def _inject_context(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    event_dict.setdefault("request_id", request_id_var.get())
    if user_id_var.get():
        event_dict.setdefault("user_id", user_id_var.get())
    if company_id_var.get():
        event_dict.setdefault("company_id", company_id_var.get())
    return event_dict


def _scrub_processor(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    scrubbed: dict[str, Any] = scrub(event_dict)
    return scrubbed


def configure_logging(*, level: str = "INFO", json_output: bool = True) -> None:
    """Install structlog and route stdlib logging through it."""
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    for noisy in ("uvicorn.access", "sqlalchemy.engine", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        _inject_context,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _scrub_processor,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    processors.append(
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]


def new_request_id() -> str:
    return uuid.uuid4().hex


@contextmanager
def bind_request_context(
    request_id: str, *, user_id: str = "", company_id: str = ""
) -> Iterator[None]:
    """Bind correlation ids for the duration of a request."""
    tokens = [
        request_id_var.set(request_id),
        user_id_var.set(user_id),
        company_id_var.set(company_id),
    ]
    try:
        yield
    finally:
        request_id_var.reset(tokens[0])
        user_id_var.reset(tokens[1])
        company_id_var.reset(tokens[2])
