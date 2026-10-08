"""Standard API response envelopes and pagination.

Consistency matters more than creativity here: the frontend should be able to
parse any response without knowing which endpoint produced it.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import math
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class APIResponse(BaseModel, Generic[T]):
    """Envelope for single resources."""

    model_config = ConfigDict(populate_by_name=True)

    data: T
    request_id: str | None = Field(default=None, alias="request_id")


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str | None = None
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    """The single error shape documented in docs/api.md."""

    error: ErrorBody


class AckResponse(BaseModel):
    """A bare acknowledgement for an action with no response body."""

    ok: Literal[True] = True
    message: str | None = None
    request_id: str | None = None


class PageMeta(BaseModel):
    total: int | None = None
    limit: int
    next_cursor: str | None = None
    has_more: bool = False


class Page(BaseModel, Generic[T]):
    """Cursor-paginated collection.

    Keyset pagination is the default: OFFSET degrades badly at 1M+ rows and
    produces duplicates when rows are inserted mid-scan.
    """

    data: list[T]
    meta: PageMeta
    request_id: str | None = None


def encode_cursor(payload: dict[str, Any]) -> str:
    raw = "|".join(f"{k}={payload[k]}" for k in sorted(payload))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> dict[str, str]:
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded).decode()
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise ValueError("Malformed cursor.") from exc
    out: dict[str, str] = {}
    for part in raw.split("|"):
        key, _, value = part.partition("=")
        if key:
            out[key] = value
    return out


def build_page(
    rows: list[dict[str, Any]],
    *,
    limit: int,
    cursor_keys: tuple[str, ...],
    request_id: str | None = None,
    total: int | None = None,
) -> Page[Any]:
    """Build a page from a keyset-limited result set.

    `rows` must be fetched with `LIMIT limit + 1` so `has_more` is exact.
    """
    has_more = len(rows) > limit
    items = rows[:limit]

    next_cursor: str | None = None
    if has_more and items:
        last = items[-1]
        if all(k in last for k in cursor_keys):
            next_cursor = encode_cursor({k: _cursor_value(last[k]) for k in cursor_keys})

    return Page(
        data=items,
        meta=PageMeta(
            total=total,
            limit=limit,
            next_cursor=next_cursor,
            has_more=has_more,
        ),
        request_id=request_id,
    )


def _cursor_value(value: Any) -> str:
    if isinstance(value, dt.datetime):
        return value.astimezone(dt.UTC).isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    return str(value)


def clamp_limit(requested: int | None, *, default: int = 50, maximum: int = 200) -> int:
    """Bounds page size. A client cannot ask for the whole table."""
    if requested is None:
        return default
    return max(1, min(requested, maximum))


def page_window(page_size: int) -> int:
    """Rows to fetch for a page: one extra row reveals whether more exist."""
    return page_size + 1


def total_pages(total: int, page_size: int) -> int:
    return math.ceil(total / page_size) if page_size else 0
