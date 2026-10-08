"""1:1 messaging between connected users.

Only connected users may message each other: opening a conversation and
sending both require an ACCEPTED connection (or messaging oneself, which the
spec forbids implicitly — self-conversations are rejected). Read state rides
on `conversation_members.last_read_at`; search is scoped to the caller's own
conversations. The `conversation_members` join and `kind` column already
support future group messaging; this service only creates DIRECT threads.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.errors import (
    BusinessRuleViolationError,
    ResourceNotFoundError,
    ValidationError,
)
from app.services import audit
from app.services.lookup import resolve_user_public_id


async def _require_messagable(conn: AsyncConnection, a: uuid.UUID, b: uuid.UUID) -> None:
    if a == b:
        raise BusinessRuleViolationError(
            "You cannot message yourself.", details={"reason": "SELF_MESSAGE"}
        )
    blocked = (
        await conn.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM public.user_blocks
                     WHERE (blocker_id = :a AND blocked_id = :b)
                        OR (blocker_id = :b AND blocked_id = :a)
                ) AS blocked
                """
            ),
            {"a": a, "b": b},
        )
    ).scalar()
    if blocked:
        raise BusinessRuleViolationError(
            "Messaging is not available with this user.", details={"reason": "USER_BLOCKED"}
        )
    connected = (
        await conn.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM public.connections
                     WHERE status = 'ACCEPTED'
                       AND ((requester_id = :a AND addressee_id = :b)
                         OR (requester_id = :b AND addressee_id = :a))
                ) AS connected
                """
            ),
            {"a": a, "b": b},
        )
    ).scalar()
    if not connected:
        raise BusinessRuleViolationError(
            "Only connected users can message each other.",
            details={"reason": "NOT_CONNECTED"},
        )


async def _member_check(
    conn: AsyncConnection, conversation_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    member = (
        await conn.execute(
            text(
                """
                SELECT 1 FROM public.conversation_members
                 WHERE conversation_id = :cid AND user_id = :uid
                """
            ),
            {"cid": conversation_id, "uid": user_id},
        )
    ).scalar_one_or_none()
    if member is None:
        raise ResourceNotFoundError("Conversation not found.")


async def open_conversation(
    conn: AsyncConnection,
    *,
    actor_user_id: uuid.UUID,
    target_public_id: str,
    request_id: str,
) -> dict[str, Any]:
    """Open (or return) the DIRECT thread with the target user."""
    from app.services.social import _assert_not_blocked

    target = await resolve_user_public_id(conn, target_public_id)
    await _require_messagable(conn, actor_user_id, target)
    await _assert_not_blocked(conn, actor_user_id, target, action="conversation")

    existing = (
        (
            await conn.execute(
                text(
                    """
                    SELECT c.id::text AS id, c.public_id
                      FROM public.conversations c
                      JOIN public.conversation_members m1
                        ON m1.conversation_id = c.id AND m1.user_id = :a
                      JOIN public.conversation_members m2
                        ON m2.conversation_id = c.id AND m2.user_id = :b
                     WHERE c.kind = 'DIRECT' AND c.deleted_at IS NULL
                    """
                ),
                {"a": actor_user_id, "b": target},
            )
        )
        .mappings()
        .first()
    )
    if existing is not None:
        return {"id": str(existing["id"]), "public_id": str(existing["public_id"])}

    row = (
        (
            await conn.execute(
                text(
                    """
                    INSERT INTO public.conversations (kind, created_by)
                    VALUES ('DIRECT', :actor)
                    RETURNING id::text AS id, public_id
                    """
                ),
                {"actor": actor_user_id},
            )
        )
        .mappings()
        .first()
    )
    await conn.execute(
        text(
            """
            INSERT INTO public.conversation_members (conversation_id, user_id, role)
            VALUES (CAST(:cid AS uuid), CAST(:a AS uuid), 'MEMBER'),
                   (CAST(:cid AS uuid), CAST(:b AS uuid), 'MEMBER')
            """
        ),
        {"cid": row["id"], "a": actor_user_id, "b": target},
    )
    await audit.record(
        conn,
        action="social.conversation_opened",
        resource_type="conversation",
        resource_id=row["id"],
        resource_public_id=str(row["public_id"]),
        actor_user_id=actor_user_id,
        request_id=request_id,
    )
    return {"id": str(row["id"]), "public_id": str(row["public_id"])}


async def list_conversations(
    conn: AsyncConnection, *, actor_user_id: uuid.UUID, limit: int
) -> list[dict[str, Any]]:
    rows = (
        (
            await conn.execute(
                text(
                    """
                    SELECT c.id::text AS id, c.public_id, c.kind, c.title,
                           c.last_message_at, c.message_count,
                           m.last_read_at,
                           (SELECT count(*) FROM public.messages msg
                             WHERE msg.conversation_id = c.id
                               AND msg.created_at
                                   > COALESCE(m.last_read_at, '-infinity'::timestamptz)
                               AND msg.sender_id <> :uid
                               AND msg.deleted_at IS NULL) AS unread_count,
                           (SELECT u.public_id FROM public.conversation_members m2
                             JOIN public.users u ON u.id = m2.user_id
                            WHERE m2.conversation_id = c.id AND m2.user_id <> :uid
                            LIMIT 1) AS peer_public_id
                      FROM public.conversations c
                      JOIN public.conversation_members m
                        ON m.conversation_id = c.id AND m.user_id = :uid
                     WHERE c.deleted_at IS NULL
                     ORDER BY c.last_message_at DESC NULLS LAST, c.created_at DESC
                     LIMIT :limit
                    """
                ),
                {"uid": actor_user_id, "limit": limit},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def send_message(
    conn: AsyncConnection,
    *,
    actor_user_id: uuid.UUID,
    conversation_public_id: str,
    request_id: str,
    ip_address: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    content = str(payload.get("content") or "").strip()
    if not content and not payload.get("document_id"):
        raise ValidationError("A message needs text or an attachment.")
    if len(content) > 5000:
        raise ValidationError("Messages are limited to 5000 characters.")

    conv = (
        (
            await conn.execute(
                text("SELECT id FROM public.conversations WHERE public_id = :pid"),
                {"pid": conversation_public_id},
            )
        )
        .mappings()
        .first()
    )
    if conv is None:
        raise ResourceNotFoundError("Conversation not found.")
    await _member_check(conn, conv["id"], actor_user_id)

    row = (
        (
            await conn.execute(
                text(
                    """
                    INSERT INTO public.messages (conversation_id, sender_id, content, document_id)
                    VALUES (:cid, :sender, :content, :document_id)
                    RETURNING id::text AS id, created_at
                    """
                ),
                {
                    "cid": conv["id"],
                    "sender": actor_user_id,
                    "content": content or None,
                    "document_id": payload.get("document_id"),
                },
            )
        )
        .mappings()
        .first()
    )
    await conn.execute(
        text(
            """
            UPDATE public.conversations
               SET last_message_at = now(), message_count = message_count + 1,
                   updated_at = now()
             WHERE id = :cid
            """
        ),
        {"cid": conv["id"]},
    )
    await audit.record(
        conn,
        action="social.message_sent",
        resource_type="conversation",
        resource_id=conv["id"],
        actor_user_id=actor_user_id,
        request_id=request_id,
        ip_address=ip_address,
    )
    return {"id": str(row["id"]), "created_at": row["created_at"]}


async def list_messages(
    conn: AsyncConnection,
    *,
    actor_user_id: uuid.UUID,
    conversation_public_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    conv = (
        (
            await conn.execute(
                text("SELECT id FROM public.conversations WHERE public_id = :pid"),
                {"pid": conversation_public_id},
            )
        )
        .mappings()
        .first()
    )
    if conv is None:
        raise ResourceNotFoundError("Conversation not found.")
    await _member_check(conn, conv["id"], actor_user_id)
    await conn.execute(
        text(
            """
            UPDATE public.conversation_members SET last_read_at = now()
             WHERE conversation_id = :cid AND user_id = :uid
            """
        ),
        {"cid": conv["id"], "uid": actor_user_id},
    )
    rows = (
        (
            await conn.execute(
                text(
                    """
                    SELECT m.id::text AS id, u.public_id AS sender_public_id,
                           m.content, m.document_id, m.reply_to_id::text AS reply_to_id,
                           m.edited_at, m.created_at
                      FROM public.messages m
                      JOIN public.users u ON u.id = m.sender_id
                     WHERE m.conversation_id = :cid AND m.deleted_at IS NULL
                     ORDER BY m.created_at ASC
                     LIMIT :limit
                    """
                ),
                {"cid": conv["id"], "limit": limit},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def search_messages(
    conn: AsyncConnection, *, actor_user_id: uuid.UUID, query: str, limit: int
) -> list[dict[str, Any]]:
    if len(query.strip()) < 2:
        raise ValidationError("Search needs at least 2 characters.")
    rows = (
        (
            await conn.execute(
                text(
                    """
                    SELECT m.id::text AS id, c.public_id AS conversation_public_id,
                           u.public_id AS sender_public_id, m.content, m.created_at
                      FROM public.messages m
                      JOIN public.conversations c ON c.id = m.conversation_id
                      JOIN public.conversation_members mine
                        ON mine.conversation_id = c.id AND mine.user_id = :uid
                      JOIN public.users u ON u.id = m.sender_id
                     WHERE m.deleted_at IS NULL AND c.deleted_at IS NULL
                       AND m.content ILIKE :q
                     ORDER BY m.created_at DESC
                     LIMIT :limit
                    """
                ),
                {"uid": actor_user_id, "q": f"%{query.strip()}%", "limit": limit},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]
