"""Professional network: posts, comments, reactions, shares, connections.

Visibility is enforced by RLS (posts_read and friends evaluate the caller's
session identity), so these services only add business rules: authorship for
edits, connection state for messaging-adjacent actions, and audit rows.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.errors import (
    BusinessRuleViolationError,
    PermissionDeniedError,
    ResourceNotFoundError,
    ValidationError,
)
from app.services import audit

_POST_SELECT = """
    SELECT p.id, p.public_id, p.author_id, p.company_id, p.content, p.post_type,
           p.visibility, p.document_id, p.project_id, p.reaction_count,
           p.comment_count, p.share_count, p.edited_at, p.created_at, p.updated_at,
           u.public_id AS author_public_id,
           NULLIF(TRIM(u.first_name || ' ' || u.last_name), '') AS author_name,
           c.public_id AS company_public_id,
           c.display_name AS company_name
      FROM public.posts p
      JOIN public.users u ON u.id = p.author_id
      LEFT JOIN public.companies c ON c.id = p.company_id
"""


def _post_from_row(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["author_id"] = data.pop("author_public_id")
    return data


async def _assert_not_blocked(
    conn: AsyncConnection, a: uuid.UUID, b: uuid.UUID, *, action: str
) -> None:
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
            f"This {action} is not available.",
            details={"reason": "USER_BLOCKED"},
        )


async def _connection_state(
    conn: AsyncConnection, viewer_id: uuid.UUID, target_id: uuid.UUID
) -> str:
    row = (
        (
            await conn.execute(
                text(
                    """
                    SELECT status FROM public.connections
                     WHERE (requester_id = :v AND addressee_id = :t)
                        OR (requester_id = :t AND addressee_id = :v)
                    """
                ),
                {"v": viewer_id, "t": target_id},
            )
        )
        .mappings()
        .first()
    )
    return str(row["status"]) if row else "NONE"


# =============================================================================
# posts
# =============================================================================
async def create_post(
    conn: AsyncConnection,
    *,
    actor_user_id: uuid.UUID,
    company_id: uuid.UUID | None,
    request_id: str,
    ip_address: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Create a personal post, or publish one as a company.

    The two identities stay distinct in the row: `author_id` is always the
    human actor, `company_id` is the publishing identity and is set only for
    company posts. Publishing as a company requires the `posts.create` grant
    in that company; without it the author can still post personally, but the
    post carries no company identity.
    """
    content = str(payload.get("content") or "").strip()
    if not (1 <= len(content) <= 5000):
        raise ValidationError("Post content must be 1-5000 characters.")
    post_type = str(payload.get("post_type") or "STANDARD").upper()
    if post_type not in {"STANDARD", "ARTICLE", "QUESTION", "POLL", "ANNOUNCEMENT"}:
        raise ValidationError(f"Unknown post type: {post_type}.")
    visibility = str(payload.get("visibility") or "PUBLIC").upper()
    if visibility not in {"PUBLIC", "CONNECTIONS", "PRIVATE"}:
        raise ValidationError(f"Unknown visibility: {visibility}.")

    as_company = bool(payload.get("as_company", False))
    if as_company:
        if company_id is None:
            raise ValidationError("Choose a company to publish as.")
        allowed = (
            await conn.execute(
                text("SELECT app.has_permission(:cid, 'posts.create', :uid) AS ok"),
                {"cid": company_id, "uid": actor_user_id},
            )
        ).scalar()
        if not allowed:
            raise PermissionDeniedError("Publishing as this company requires posts.create.")
    else:
        company_id = None

    row = (
        (
            await conn.execute(
                text(
                    """
                    INSERT INTO public.posts
                      (author_id, company_id, content, post_type, visibility,
                       document_id, project_id)
                    VALUES (:author, :company, :content, :post_type,
                            CAST(:visibility AS visibility),
                            :document_id, :project_id)
                    RETURNING public_id
                    """
                ),
                {
                    "author": actor_user_id,
                    "company": company_id,
                    "content": content,
                    "post_type": post_type,
                    "visibility": visibility,
                    "document_id": payload.get("document_id"),
                    "project_id": payload.get("project_id"),
                },
            )
        )
        .mappings()
        .first()
    )
    public_id = str(row["public_id"])
    created = await get_post(conn, viewer_id=actor_user_id, public_id=public_id)
    await audit.record(
        conn,
        action="social.post_created",
        resource_type="post",
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        request_id=request_id,
        ip_address=ip_address,
    )
    return created


async def get_post(
    conn: AsyncConnection, *, viewer_id: uuid.UUID, public_id: str
) -> dict[str, Any]:
    row = (
        (
            await conn.execute(
                text(f"{_POST_SELECT} WHERE p.public_id = :pid AND p.deleted_at IS NULL"),
                {"pid": public_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        # Indistinguishable from "not permitted": enumerating posts by id
        # must not reveal which ones exist.
        raise ResourceNotFoundError("Post not found.")
    return _post_from_row(row)


async def list_feed(
    conn: AsyncConnection,
    *,
    viewer_id: uuid.UUID,
    limit: int,
    cursor_keys: dict[str, str],
) -> list[dict[str, Any]]:
    """Own posts, connection posts, and public professional posts, newest first."""
    where = ["p.deleted_at IS NULL"]
    params: dict[str, Any] = {"viewer": viewer_id, "limit": limit + 1}
    if cursor_keys.get("created_at"):
        where.append("(p.created_at, p.id) < (:cur_created, CAST(:cur_id AS uuid))")
        params["cur_created"] = cursor_keys["created_at"]
        params["cur_id"] = cursor_keys.get("id", "")
    rows = (
        (
            await conn.execute(
                text(
                    f"""
                    {_POST_SELECT}
                     WHERE {" AND ".join(where)}
                       AND (
                             p.author_id = :viewer
                          OR p.visibility = 'PUBLIC'
                          OR (p.visibility = 'CONNECTIONS' AND EXISTS (
                                SELECT 1 FROM public.connections c
                                 WHERE c.status = 'ACCEPTED'
                                   AND ((c.requester_id = :viewer
                                         AND c.addressee_id = p.author_id)
                                     OR (c.requester_id = p.author_id
                                         AND c.addressee_id = :viewer))))
                       )
                     ORDER BY p.created_at DESC, p.id DESC
                     LIMIT :limit
                    """  # noqa: S608 - fixed fragment assembled from allowlisted parts
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    return [_post_from_row(r) for r in rows]


async def update_post(
    conn: AsyncConnection,
    *,
    actor_user_id: uuid.UUID,
    public_id: str,
    request_id: str,
    ip_address: str | None,
    changes: dict[str, Any],
) -> dict[str, Any]:
    row = (
        (
            await conn.execute(
                text("SELECT id, author_id FROM public.posts WHERE public_id = :pid"),
                {"pid": public_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None or row["author_id"] != actor_user_id:
        raise ResourceNotFoundError("Post not found.")
    content = str(changes.get("content") or "").strip()
    if not (1 <= len(content) <= 5000):
        raise ValidationError("Post content must be 1-5000 characters.")
    await conn.execute(
        text("UPDATE public.posts SET content = :c, edited_at = now() WHERE id = :rid"),
        {"c": content, "rid": row["id"]},
    )
    await audit.record(
        conn,
        action="social.post_updated",
        resource_type="post",
        resource_public_id=public_id,
        actor_user_id=actor_user_id,
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_post(conn, viewer_id=actor_user_id, public_id=public_id)


async def delete_post(
    conn: AsyncConnection,
    *,
    actor_user_id: uuid.UUID,
    public_id: str,
    request_id: str,
    ip_address: str | None,
) -> None:
    row = (
        (
            await conn.execute(
                text("SELECT id, author_id FROM public.posts WHERE public_id = :pid"),
                {"pid": public_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None or row["author_id"] != actor_user_id:
        raise ResourceNotFoundError("Post not found.")
    await conn.execute(
        text("UPDATE public.posts SET deleted_at = now() WHERE id = :rid"), {"rid": row["id"]}
    )
    await audit.record(
        conn,
        action="social.post_deleted",
        resource_type="post",
        resource_public_id=public_id,
        actor_user_id=actor_user_id,
        request_id=request_id,
        ip_address=ip_address,
    )


async def toggle_reaction(
    conn: AsyncConnection,
    *,
    actor_user_id: uuid.UUID,
    public_id: str,
    reaction: str,
    request_id: str,
) -> dict[str, Any]:
    reaction = str(reaction or "LIKE").upper()
    if reaction not in {"LIKE", "CELEBRATE", "SUPPORT", "INSIGHTFUL", "CURIOUS"}:
        raise ValidationError(f"Unknown reaction: {reaction}.")
    post = await get_post(conn, viewer_id=actor_user_id, public_id=public_id)
    # One reaction per user per post: the PK spans (post, user, reaction), so
    # replace rather than upsert.
    await conn.execute(
        text(
            """
            DELETE FROM public.post_reactions
             WHERE post_id = (SELECT id FROM public.posts WHERE public_id = :pid)
               AND user_id = :uid
            """
        ),
        {"pid": public_id, "uid": actor_user_id},
    )
    await conn.execute(
        text(
            """
            INSERT INTO public.post_reactions (post_id, user_id, reaction)
            VALUES ((SELECT id FROM public.posts WHERE public_id = :pid), :uid, :reaction)
            """
        ),
        {"pid": public_id, "uid": actor_user_id, "reaction": reaction},
    )
    await audit.record(
        conn,
        action="social.post_reacted",
        resource_type="post",
        resource_public_id=public_id,
        actor_user_id=actor_user_id,
        new_values={"reaction": reaction},
        request_id=request_id,
    )
    return await get_post(conn, viewer_id=actor_user_id, public_id=post["public_id"])


async def add_comment(
    conn: AsyncConnection,
    *,
    actor_user_id: uuid.UUID,
    public_id: str,
    request_id: str,
    ip_address: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    content = str(payload.get("content") or "").strip()
    if not (1 <= len(content) <= 2000):
        raise ValidationError("Comment must be 1-2000 characters.")
    post = await get_post(conn, viewer_id=actor_user_id, public_id=public_id)
    row = (
        (
            await conn.execute(
                text(
                    """
                    INSERT INTO public.post_comments (post_id, author_id, parent_id, content)
                    VALUES ((SELECT id FROM public.posts WHERE public_id = :pid),
                            :uid, :parent, :content)
                    RETURNING id
                    """
                ),
                {
                    "pid": public_id,
                    "uid": actor_user_id,
                    "parent": payload.get("parent_id"),
                    "content": content,
                },
            )
        )
        .mappings()
        .first()
    )
    await audit.record(
        conn,
        action="social.comment_created",
        resource_type="post",
        resource_public_id=public_id,
        actor_user_id=actor_user_id,
        request_id=request_id,
        ip_address=ip_address,
    )
    return {"id": str(row["id"]), "post_id": post["public_id"]}


async def list_comments(
    conn: AsyncConnection, *, viewer_id: uuid.UUID, public_id: str, limit: int
) -> list[dict[str, Any]]:
    await get_post(conn, viewer_id=viewer_id, public_id=public_id)
    rows = (
        (
            await conn.execute(
                text(
                    """
                    SELECT c.id::text AS id, u.public_id AS author_public_id,
                           NULLIF(TRIM(u.first_name || ' ' || u.last_name), '') AS author_name,
                           c.content, c.created_at
                      FROM public.post_comments c
                      JOIN public.users u ON u.id = c.author_id
                     WHERE c.post_id = (SELECT id FROM public.posts WHERE public_id = :pid)
                       AND c.deleted_at IS NULL
                     ORDER BY c.created_at ASC
                     LIMIT :limit
                    """
                ),
                {"pid": public_id, "limit": limit},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def share_post(
    conn: AsyncConnection,
    *,
    actor_user_id: uuid.UUID,
    public_id: str,
    request_id: str,
    ip_address: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    post = await get_post(conn, viewer_id=actor_user_id, public_id=public_id)
    visibility = str(payload.get("visibility") or "PUBLIC").upper()
    if visibility not in {"PUBLIC", "CONNECTIONS", "PRIVATE"}:
        raise ValidationError(f"Unknown visibility: {visibility}.")
    row = (
        (
            await conn.execute(
                text(
                    """
                    INSERT INTO public.post_shares
                      (post_id, user_id, shared_post_id, commentary, visibility, company_id)
                    VALUES ((SELECT id FROM public.posts WHERE public_id = :pid),
                            :uid, NULL, :commentary, CAST(:visibility AS visibility), :company)
                    RETURNING id
                    """
                ),
                {
                    "pid": public_id,
                    "uid": actor_user_id,
                    "commentary": payload.get("commentary"),
                    "visibility": visibility,
                    "company": payload.get("company_id"),
                },
            )
        )
        .mappings()
        .first()
    )
    await audit.record(
        conn,
        action="social.post_shared",
        resource_type="post",
        resource_public_id=post["public_id"],
        actor_user_id=actor_user_id,
        request_id=request_id,
        ip_address=ip_address,
    )
    return {"id": str(row["id"]), "post_id": post["public_id"]}


# =============================================================================
# connections
# =============================================================================
async def send_request(
    conn: AsyncConnection,
    *,
    actor_user_id: uuid.UUID,
    target_public_id: str,
    request_id: str,
    message: str | None,
) -> dict[str, Any]:
    from app.services.lookup import resolve_user_public_id

    target = await resolve_user_public_id(conn, target_public_id)
    if target == actor_user_id:
        raise BusinessRuleViolationError(
            "You cannot connect with yourself.", details={"reason": "SELF_CONNECTION"}
        )
    await _assert_not_blocked(conn, actor_user_id, target, action="connection")
    state = await _connection_state(conn, actor_user_id, target)
    if state == "ACCEPTED":
        raise BusinessRuleViolationError(
            "You are already connected.", details={"reason": "ALREADY_CONNECTED"}
        )
    pending = (
        await conn.execute(
            text(
                """
                SELECT id FROM public.connection_requests
                 WHERE requester_id = :requester AND addressee_id = :addressee
                   AND status = 'PENDING'
                """
            ),
            {"requester": actor_user_id, "addressee": target},
        )
    ).scalar_one_or_none()
    if pending is None:
        await conn.execute(
            text(
                """
                INSERT INTO public.connection_requests (requester_id, addressee_id, message, status)
                VALUES (:requester, :addressee, :message, 'PENDING')
                """
            ),
            {"requester": actor_user_id, "addressee": target, "message": message},
        )
    await audit.record(
        conn,
        action="social.connection_requested",
        resource_type="user",
        resource_id=target,
        actor_user_id=actor_user_id,
        request_id=request_id,
    )
    return {"state": "PENDING"}


async def respond_request(
    conn: AsyncConnection,
    *,
    actor_user_id: uuid.UUID,
    requester_public_id: str,
    request_id: str,
    accept: bool,
) -> dict[str, Any]:
    from app.services.lookup import resolve_user_public_id

    requester = await resolve_user_public_id(conn, requester_public_id)
    result = await conn.execute(
        text(
            """
            UPDATE public.connection_requests
               SET status = :status, responded_at = now()
             WHERE requester_id = :requester AND addressee_id = :actor AND status = 'PENDING'
            RETURNING id
            """
        ),
        {
            "requester": requester,
            "actor": actor_user_id,
            "status": "ACCEPTED" if accept else "DECLINED",
        },
    )
    if result.rowcount == 0:
        raise ResourceNotFoundError("Connection request not found.")
    if accept:
        # user_low/user_high are generated columns: insert only the pair and
        # let the database order them.
        await conn.execute(
            text(
                """
                INSERT INTO public.connections
                  (requester_id, addressee_id, status, accepted_at)
                VALUES (:requester, :actor, 'ACCEPTED', now())
                ON CONFLICT DO NOTHING
                """
            ),
            {"requester": requester, "actor": actor_user_id},
        )
    await audit.record(
        conn,
        action="social.connection_accepted" if accept else "social.connection_declined",
        resource_type="user",
        resource_id=requester,
        actor_user_id=actor_user_id,
        request_id=request_id,
    )
    return {"state": "ACCEPTED" if accept else "DECLINED"}


async def remove_connection(
    conn: AsyncConnection, *, actor_user_id: uuid.UUID, target_public_id: str, request_id: str
) -> None:
    from app.services.lookup import resolve_user_public_id

    target = await resolve_user_public_id(conn, target_public_id)
    await conn.execute(
        text(
            """
            UPDATE public.connections SET status = 'REMOVED'
             WHERE ((requester_id = :a AND addressee_id = :b)
                 OR (requester_id = :b AND addressee_id = :a))
               AND status = 'ACCEPTED'
            """
        ),
        {"a": actor_user_id, "b": target},
    )
    await audit.record(
        conn,
        action="social.connection_removed",
        resource_type="user",
        resource_id=target,
        actor_user_id=actor_user_id,
        request_id=request_id,
    )


async def list_connections(
    conn: AsyncConnection, *, viewer_id: uuid.UUID, limit: int
) -> list[dict[str, Any]]:
    rows = (
        (
            await conn.execute(
                text(
                    """
                    SELECT u.public_id,
                           NULLIF(TRIM(u.first_name || ' ' || u.last_name), '') AS display_name,
                           u.avatar_url, p.headline
                      FROM public.connections c
                      JOIN public.users u ON u.id = (
                        CASE WHEN c.requester_id = :viewer
                             THEN c.addressee_id ELSE c.requester_id END)
                      LEFT JOIN public.user_profiles p ON p.user_id = u.id
                     WHERE (c.requester_id = :viewer OR c.addressee_id = :viewer)
                       AND c.status = 'ACCEPTED'
                     ORDER BY c.accepted_at DESC
                     LIMIT :limit
                    """
                ),
                {"viewer": viewer_id, "limit": limit},
            )
        )
        .mappings()
        .all()
    )
    out = []
    for r in rows:
        item = dict(r)
        # headline lives on the profile in this schema.
        out.append(item)
    return out


async def list_requests(conn: AsyncConnection, *, viewer_id: uuid.UUID) -> dict[str, Any]:
    """Pending connection requests, split by direction.

    Incoming can be accepted or declined; outgoing are waiting on the other
    side. Decided requests disappear: history lives in the audit log.
    """

    async def _side(column: str) -> list[dict[str, Any]]:
        other = "requester_id" if column == "addressee_id" else "addressee_id"
        rows = (
            (
                await conn.execute(
                    text(
                        f"""
                        SELECT u.public_id,
                               NULLIF(TRIM(u.first_name || ' ' || u.last_name), '') AS display_name,
                               u.avatar_url, p.headline, r.message, r.created_at
                          FROM public.connection_requests r
                          JOIN public.users u ON u.id = r.{other}
                          LEFT JOIN public.user_profiles p ON p.user_id = u.id
                         WHERE r.{column} = :viewer AND r.status = 'PENDING'
                         ORDER BY r.created_at DESC
                        """  # noqa: S608 - column is one of two literals
                    ),
                    {"viewer": viewer_id},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    return {"incoming": await _side("addressee_id"), "outgoing": await _side("requester_id")}


async def block_user(
    conn: AsyncConnection, *, actor_user_id: uuid.UUID, target_public_id: str, request_id: str
) -> None:
    from app.services.lookup import resolve_user_public_id

    target = await resolve_user_public_id(conn, target_public_id)
    await conn.execute(
        text(
            """
            INSERT INTO public.user_blocks (blocker_id, blocked_id)
            VALUES (:blocker, :blocked)
            ON CONFLICT DO NOTHING
            """
        ),
        {"blocker": actor_user_id, "blocked": target},
    )
    await audit.record(
        conn,
        action="social.user_blocked",
        resource_type="user",
        resource_id=target,
        actor_user_id=actor_user_id,
        request_id=request_id,
    )


async def report(
    conn: AsyncConnection,
    *,
    actor_user_id: uuid.UUID,
    request_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    from app.services.lookup import resolve_user_public_id

    target_type = str(payload.get("target_type") or "").upper()
    if target_type not in {"USER", "POST", "COMMENT", "MESSAGE", "COMPANY", "DOCUMENT"}:
        raise ValidationError(f"Unknown report target: {target_type}.")
    raw_target = payload.get("target_id")
    if target_type == "USER":
        target_id: Any = await resolve_user_public_id(conn, str(raw_target or ""))
    elif target_type == "POST":
        post_row = (
            (
                await conn.execute(
                    text("SELECT id FROM public.posts WHERE public_id = :pid"),
                    {"pid": str(raw_target or "")},
                )
            )
            .mappings()
            .first()
        )
        if post_row is None:
            raise ResourceNotFoundError("Reported post not found.")
        target_id = post_row["id"]
    else:
        try:
            target_id = uuid.UUID(str(raw_target))
        except ValueError:
            raise ValidationError("Report target must be a valid id.") from None
    row = (
        (
            await conn.execute(
                text(
                    """
                    INSERT INTO public.reports
                      (reporter_id, target_type, target_id, reason, details, status)
                    VALUES (:reporter, :target_type, :target_id, :reason, :details, 'OPEN')
                    RETURNING id
                    """
                ),
                {
                    "reporter": actor_user_id,
                    "target_type": target_type,
                    "target_id": target_id,
                    "reason": payload.get("reason", ""),
                    "details": json.dumps(payload.get("details") or {}),
                },
            )
        )
        .mappings()
        .first()
    )
    await audit.record(
        conn,
        action="social.content_reported",
        resource_type="report",
        resource_id=row["id"],
        actor_user_id=actor_user_id,
        new_values={"target_type": target_type},
        request_id=request_id,
    )
    return {"id": str(row["id"])}
