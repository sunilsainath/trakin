"""Business notifications: the right people, only when listening.

Joining, role changes, SOW decisions and uploads emit outbox events whose
recipients are derived from the referenced records — never from the event
body — and a muted category stays muted. Everything below runs inside the
rolled-back `conn` transaction.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.services import events as event_service

pytestmark = pytest.mark.integration


async def _membership_id(conn, tenants) -> str:
    return (
        await conn.execute(
            text(
                "SELECT m.id::text FROM public.company_memberships m "
                "WHERE m.company_id = :cid AND m.user_id = :uid"
            ),
            {"cid": tenants["admin"].company_id, "uid": tenants["worker"].user_id},
        )
    ).scalar_one()


async def _notification_count(conn, user_id, event_type: str) -> int:
    return (
        await conn.execute(
            text(
                "SELECT count(*) FROM platform.notifications WHERE user_id = :uid AND type = :type"
            ),
            {"uid": user_id, "type": event_type},
        )
    ).scalar_one()


async def test_member_joined_notifies_admins_unless_muted(conn, tenants) -> None:
    admin, worker = tenants["admin"], tenants["worker"]
    membership_id = await _membership_id(conn, tenants)

    recipients = await event_service._recipients_for(
        conn,
        "membership",
        membership_id,
        {"company_id": str(admin.company_id), "user_id": str(worker.user_id)},
    )
    assert admin.user_id in recipients
    assert worker.user_id in recipients

    await event_service._insert_notifications(
        conn,
        "MEMBER_JOINED",
        "W joined Alpha",
        None,
        "INFO",
        "membership",
        membership_id,
        {"company_id": str(admin.company_id), "user_id": str(worker.user_id)},
    )
    assert await _notification_count(conn, admin.user_id, "MEMBER_JOINED") == 1

    await conn.execute(
        text(
            "INSERT INTO platform.notification_preferences (user_id, category, in_app, email, push)"
            " VALUES (:uid, 'SYSTEM', false, false, false)"
            " ON CONFLICT (user_id, category) DO UPDATE SET in_app = false"
        ),
        {"uid": admin.user_id},
    )
    await event_service._insert_notifications(
        conn,
        "MEMBER_JOINED",
        "W joined Alpha",
        None,
        "INFO",
        "membership",
        membership_id,
        {"company_id": str(admin.company_id), "user_id": str(worker.user_id)},
    )
    assert await _notification_count(conn, admin.user_id, "MEMBER_JOINED") == 1


async def test_role_assigned_reaches_the_member(conn, tenants) -> None:
    admin, worker = tenants["admin"], tenants["worker"]
    membership_id = await _membership_id(conn, tenants)

    recipients = await event_service._recipients_for(
        conn,
        "membership",
        membership_id,
        {"company_id": str(admin.company_id), "user_id": str(worker.user_id)},
    )
    assert worker.user_id in recipients

    title, body, severity = event_service._render("ROLE_ASSIGNED", {"role_key": "CONTRACT_MANAGER"})
    assert "changed" in title and "CONTRACT_MANAGER" in (body or "")


async def test_document_uploaded_notifies_admins(conn, tenants) -> None:
    from app.services import documents as document_service

    admin = tenants["admin"]
    document = await document_service.create_document(
        conn,
        company_id=admin.company_id,
        actor_user_id=admin.user_id,
        request_id="pytest",
        ip_address=None,
        title="Policy",
        doc_type="CONTRACT",
        description=None,
        related_type=None,
        related_public_id=None,
    )
    internal_id = (
        await conn.execute(
            text("SELECT id::text FROM public.documents WHERE public_id = :pid"),
            {"pid": document["public_id"]},
        )
    ).scalar_one()
    recipients = await event_service._recipients_for(
        conn, "document", internal_id, {"company_id": str(admin.company_id)}
    )
    assert admin.user_id in recipients


async def test_sow_decision_reaches_both_sides(conn, skeleton, tenants) -> None:
    admin, worker = tenants["admin"], tenants["worker"]
    recipients = await event_service._recipients_for(
        conn, "sow", skeleton.sow, {"company_id": str(admin.company_id)}
    )
    assert admin.user_id in recipients
    assert worker.user_id in recipients

    title, _, _ = event_service._render(
        "SOW_STATUS_CHANGED", {"public_id": "S00000001", "old": "PENDING_APPROVAL", "new": "ACTIVE"}
    )
    assert "S00000001" in title


async def test_emit_event_is_idempotent(conn, tenants) -> None:
    admin = tenants["admin"]
    membership_id = await _membership_id(conn, tenants)
    for _ in range(2):
        await event_service.emit_event(
            conn,
            event_type="MEMBER_JOINED",
            aggregate_type="membership",
            aggregate_id=membership_id,
            company_id=admin.company_id,
            payload={"company_id": str(admin.company_id)},
        )
    count = (
        await conn.execute(
            text(
                "SELECT count(*) FROM platform.outbox_events "
                "WHERE event_type = 'MEMBER_JOINED' AND aggregate_id = :aid"
            ),
            {"aid": membership_id},
        )
    ).scalar_one()
    assert count == 1
