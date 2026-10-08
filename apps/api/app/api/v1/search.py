"""Global search.

Delegates to `app.search_all`, which applies the caller's permissions inside SQL
before ranking. The API adds nothing: a hit the user may not see is not returned
and then filtered here, because that would still disclose that it exists.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.api.deps import RequestContext, require_company_member
from app.core.logging import get_logger
from app.schemas.common import Page, build_page, clamp_limit, decode_cursor
from app.schemas.identity import SearchHit

router = APIRouter(prefix="/search", tags=["search"])
logger = get_logger(__name__)

Context = Annotated[tuple[RequestContext, AsyncConnection], Depends(require_company_member)]

ENTITY_TYPES = (
    "PERSON",
    "COMPANY",
    "PROJECT",
    "SOW",
    "CONTRACT",
    "INVOICE",
    "DOCUMENT",
    "POST",
)


@router.get("", response_model=Page[SearchHit], summary="Permission-aware global search")
async def search(
    ctx_and_conn: Context,
    q: str = Query(..., min_length=2, max_length=200),
    limit: int = Query(20, ge=1, le=50),
    cursor: str | None = Query(None),
    types: str | None = Query(None, description="Comma-separated entity types to include"),
) -> Page[Any]:
    """Search across people, companies, projects, SOWs, contracts, invoices,
    documents and posts.

    Results are limited to what the caller may read inside the active company,
    plus public people and companies for discovery.
    """
    ctx, conn = ctx_and_conn

    entity_types: list[str] | None = None
    if types:
        requested = [t.strip().upper() for t in types.split(",") if t.strip()]
        unknown = [t for t in requested if t not in ENTITY_TYPES]
        if unknown:
            from app.core.errors import ValidationError

            raise ValidationError(
                "Unknown search entity type(s).",
                details={"unknown": unknown, "allowed": list(ENTITY_TYPES)},
            )
        entity_types = requested

    page_size = clamp_limit(limit, default=20, maximum=50)

    # Cursor carries the rank of the last row; a lower bound is approximated by
    # skipping the prefix, which is acceptable because the result set is capped.
    offset = 0
    if cursor:
        try:
            offset = int(decode_cursor(cursor).get("offset", "0"))
        except ValueError:
            from app.core.errors import ValidationError

            raise ValidationError("Invalid cursor.") from None

    rows = (
        (
            await conn.execute(
                text(
                    """
                SELECT entity_type, public_id, title, subtitle, rank
                  FROM app.search_all(
                         :q,
                         CAST(:cid AS uuid),
                         CAST(:types AS text[]),
                         :limit + :offset
                  )
                """
                ),
                {
                    "q": q,
                    "cid": ctx.company_id,
                    "types": entity_types,
                    "limit": page_size,
                    "offset": offset,
                },
            )
        )
        .mappings()
        .all()
    )

    window = [dict(r) for r in rows[offset:]]

    return build_page(
        window,
        limit=page_size,
        cursor_keys=("rank",),
        request_id=ctx.request_id,
    )
