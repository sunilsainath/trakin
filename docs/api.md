# MyTrakin — API conventions

Base path `/api/v1` (`app/api/v1/__init__.py`). Docs at `/docs`, `/redoc`,
`/openapi.json`.

## Envelope

Success: the resource (or `{data, request_id}` / paged `Page[T]`).
Failure, always: `{"error": {"code", "message", "request_id"}}` with a matching
`X-Request-Id` response header — including the 500 path (see
`test_a_server_error_response_still_carries_the_request_id`). Stack traces
never cross the boundary.

## Conventions

- Pagination: `limit`/`cursor` (keyset) via `schemas/common.py`; no unbounded
  lists.
- Filtering/sorting per resource; `201` on creation with the created object.
- Company context travels in `X-Company-Public-Id`, resolved through
  membership (never trusted blindly); unknown ids behave as "no context".
- Idempotency: financial writes accept `Idempotency-Key`
  (`app/core/idempotency.py`, `platform.idempotency_keys`); schedules and
  webhooks use content-derived keys (`schedule:<id>:<date>`,
  `webhook:<provider>:<event>`).
- Rate limits: `app/core/rate_limit.py` (Redis counters).
- Validation: Pydantic v2 strict models — invalid bodies are typed 422s, never
  500s (regression test: `POST /invoices/{id}/credit-notes` with `{}`).
- Webhooks authenticate by HMAC signature, not bearer tokens
  (`POST /payments/webhooks/{provider}`).
- Money: `Decimal` throughout (`billing.money`); JSON carries decimals as
  strings where precision matters.

## Status codes

400 validation/context · 401 auth · 403 permission/segregation ·
404 indistinguishable from "not permitted" for profiles/posts ·
409 business-rule/state conflicts · 422 schema errors ·
429 budgets/rate limits · 503 disabled capability or unconfigured integration.
