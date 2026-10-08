"""1:1 messaging between connected users.

Reading needs messages.read, sending needs messages.send, and the service
additionally requires an ACCEPTED connection (or rejects blocked pairs).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncConnection

from app.api.deps import RequestContext, require_permission
from app.services import messaging as messaging_service

router = APIRouter(tags=["messaging"])

MessagesRead = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("messages.read"))
]
MessagesSend = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("messages.send"))
]


@router.get("/conversations", summary="List my conversations")
async def list_conversations(
    ctx_and_conn: MessagesRead, limit: int = Query(50, ge=1, le=200)
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    rows = await messaging_service.list_conversations(conn, actor_user_id=ctx.user_id, limit=limit)
    return {"data": rows, "request_id": ctx.request_id}


@router.post("/conversations", status_code=201, summary="Open a conversation")
async def open_conversation(ctx_and_conn: MessagesSend, payload: dict[str, Any]) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await messaging_service.open_conversation(
        conn,
        actor_user_id=ctx.user_id,
        target_public_id=str(payload.get("user_id") or ""),
        request_id=ctx.request_id,
    )


@router.get("/conversations/{conversation_id}/messages", summary="Read a thread")
async def read_thread(
    ctx_and_conn: MessagesRead,
    conversation_id: str,
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    rows = await messaging_service.list_messages(
        conn,
        actor_user_id=ctx.user_id,
        conversation_public_id=conversation_id,
        limit=limit,
    )
    return {"data": rows, "request_id": ctx.request_id}


@router.post("/conversations/{conversation_id}/messages", status_code=201, summary="Send a message")
async def send_message(
    ctx_and_conn: MessagesSend, conversation_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await messaging_service.send_message(
        conn,
        actor_user_id=ctx.user_id,
        conversation_public_id=conversation_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        payload=payload,
    )


@router.get("/messages/search", summary="Search my messages")
async def search_messages(
    ctx_and_conn: MessagesRead,
    q: str = Query(..., min_length=2, max_length=200),
    limit: int = Query(25, ge=1, le=100),
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    rows = await messaging_service.search_messages(
        conn, actor_user_id=ctx.user_id, query=q, limit=limit
    )
    return {"data": rows, "request_id": ctx.request_id}
