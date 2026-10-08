"""Professional network endpoints: posts, comments, reactions, connections.

Permission model: reading needs the read key, writing needs the write key,
and authorship/connection state is enforced in the service layer on top of RLS.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncConnection

from app.api.deps import RequestContext, require_permission
from app.core.logging import get_logger
from app.schemas.common import Page, build_page, clamp_limit, decode_cursor
from app.services import social as social_service

logger = get_logger(__name__)

router = APIRouter(tags=["social"])

PostsRead = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("posts.read"))
]
PostsCreate = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("posts.create"))
]
PostsReact = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("posts.react"))
]
PostsComment = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("posts.comment"))
]
ConnectionsRead = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("connections.read"))
]
ConnectionsWrite = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("connections.create"))
]
ConnectionsRespond = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("connections.respond"))
]
ConnectionsRemove = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("connections.remove"))
]


def _ctx_ip(ctx: RequestContext) -> str | None:
    return ctx.ip_address


# =============================================================================
# posts
# =============================================================================
@router.get("/posts", response_model=Page[dict[str, Any]], summary="Professional feed")
async def feed(
    ctx_and_conn: PostsRead,
    limit: int = Query(25, ge=1, le=100),
    cursor: str | None = Query(None),
) -> Page[Any]:
    """Own, connection and public posts, newest first."""
    ctx, conn = ctx_and_conn
    page_size = clamp_limit(limit, default=25, maximum=100)
    rows = await social_service.list_feed(
        conn,
        viewer_id=ctx.user_id,
        limit=page_size,
        cursor_keys=decode_cursor(cursor) if cursor else {},
    )
    return build_page(
        rows, limit=page_size, cursor_keys=("created_at", "id"), request_id=ctx.request_id
    )


@router.post("/posts", status_code=201, summary="Create a post")
async def create_post(ctx_and_conn: PostsCreate, payload: dict[str, Any]) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await social_service.create_post(
        conn,
        actor_user_id=ctx.user_id,
        company_id=ctx.company_id,
        request_id=ctx.request_id,
        ip_address=_ctx_ip(ctx),
        payload=payload,
    )


@router.get("/posts/{post_id}", summary="Read one post")
async def read_post(ctx_and_conn: PostsRead, post_id: str) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await social_service.get_post(conn, viewer_id=ctx.user_id, public_id=post_id)


@router.patch("/posts/{post_id}", summary="Edit own post")
async def edit_post(
    ctx_and_conn: PostsCreate, post_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await social_service.update_post(
        conn,
        actor_user_id=ctx.user_id,
        public_id=post_id,
        request_id=ctx.request_id,
        ip_address=_ctx_ip(ctx),
        changes=payload,
    )


@router.delete("/posts/{post_id}", summary="Delete own post")
async def delete_post(ctx_and_conn: PostsCreate, post_id: str) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    await social_service.delete_post(
        conn,
        actor_user_id=ctx.user_id,
        public_id=post_id,
        request_id=ctx.request_id,
        ip_address=_ctx_ip(ctx),
    )
    return {"ok": True, "request_id": ctx.request_id}


@router.post("/posts/{post_id}/reactions", summary="React to a post")
async def react(ctx_and_conn: PostsReact, post_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await social_service.toggle_reaction(
        conn,
        actor_user_id=ctx.user_id,
        public_id=post_id,
        reaction=str(payload.get("reaction") or "LIKE"),
        request_id=ctx.request_id,
    )


@router.get("/posts/{post_id}/comments", summary="List comments")
async def list_comments(
    ctx_and_conn: PostsRead, post_id: str, limit: int = Query(50, ge=1, le=200)
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    rows = await social_service.list_comments(
        conn, viewer_id=ctx.user_id, public_id=post_id, limit=limit
    )
    return {"data": rows, "request_id": ctx.request_id}


@router.post("/posts/{post_id}/comments", status_code=201, summary="Comment on a post")
async def comment(
    ctx_and_conn: PostsComment, post_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await social_service.add_comment(
        conn,
        actor_user_id=ctx.user_id,
        public_id=post_id,
        request_id=ctx.request_id,
        ip_address=_ctx_ip(ctx),
        payload=payload,
    )


@router.post("/posts/{post_id}/shares", status_code=201, summary="Share a post")
async def share(ctx_and_conn: PostsCreate, post_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await social_service.share_post(
        conn,
        actor_user_id=ctx.user_id,
        public_id=post_id,
        request_id=ctx.request_id,
        ip_address=_ctx_ip(ctx),
        payload=payload,
    )


# =============================================================================
# connections
# =============================================================================
@router.get("/connections", summary="List my connections")
async def list_connections(
    ctx_and_conn: ConnectionsRead, limit: int = Query(50, ge=1, le=200)
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    rows = await social_service.list_connections(conn, viewer_id=ctx.user_id, limit=limit)
    return {"data": rows, "request_id": ctx.request_id}


@router.post("/connections/requests", status_code=201, summary="Send a connection request")
async def send_request(ctx_and_conn: ConnectionsWrite, payload: dict[str, Any]) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await social_service.send_request(
        conn,
        actor_user_id=ctx.user_id,
        target_public_id=str(payload.get("user_id") or ""),
        request_id=ctx.request_id,
        message=payload.get("message"),
    )


@router.post("/connections/requests/{user_id}/accept", summary="Accept a request")
async def accept_request(ctx_and_conn: ConnectionsRespond, user_id: str) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await social_service.respond_request(
        conn,
        actor_user_id=ctx.user_id,
        requester_public_id=user_id,
        request_id=ctx.request_id,
        accept=True,
    )


@router.post("/connections/requests/{user_id}/decline", summary="Decline a request")
async def decline_request(ctx_and_conn: ConnectionsRespond, user_id: str) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await social_service.respond_request(
        conn,
        actor_user_id=ctx.user_id,
        requester_public_id=user_id,
        request_id=ctx.request_id,
        accept=False,
    )


@router.delete("/connections/{user_id}", summary="Remove a connection")
async def remove_connection(ctx_and_conn: ConnectionsRemove, user_id: str) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    await social_service.remove_connection(
        conn, actor_user_id=ctx.user_id, target_public_id=user_id, request_id=ctx.request_id
    )
    return {"ok": True, "request_id": ctx.request_id}


@router.post("/blocks/{user_id}", status_code=201, summary="Block a user")
async def block_user(ctx_and_conn: ConnectionsRemove, user_id: str) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    await social_service.block_user(
        conn, actor_user_id=ctx.user_id, target_public_id=user_id, request_id=ctx.request_id
    )
    return {"ok": True, "request_id": ctx.request_id}


@router.post("/reports", status_code=201, summary="Report a user or content")
async def report(ctx_and_conn: PostsRead, payload: dict[str, Any]) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await social_service.report(
        conn, actor_user_id=ctx.user_id, request_id=ctx.request_id, payload=payload
    )
