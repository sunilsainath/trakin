"""Social, messaging, schedules and webhook ingest, against the real database.

Covers the routers added after Phase 1: posts/comments/reactions/shares,
connections/blocks/reports, 1:1 messaging, recurring schedules and processor
webhook ingest. Each test proves the authorization-relevant property, not just
the happy path.

Marked `integration`: needs the real database.

Run:
    pytest apps/api/tests/test_social_messaging.py -v
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import pytest

from app.core.errors import (
    BusinessRuleViolationError,
    PermissionDeniedError,
    ResourceNotFoundError,
    ValidationError,
)

pytestmark = pytest.mark.integration


async def _post(conn, skeleton, tenant, **overrides: Any) -> dict[str, Any]:
    from app.services import social

    payload = {"content": "Shipping the billing engine today.", "visibility": "PUBLIC"}
    payload.update(overrides)
    return await social.create_post(
        conn,
        actor_user_id=tenant.user_id,
        company_id=tenant.company_id,
        request_id="pytest",
        ip_address=None,
        payload=payload,
    )


# =============================================================================
# posts
# =============================================================================
async def test_post_crud_round_trip(conn, skeleton, tenants) -> None:
    from app.services import social

    tenant = tenants["admin"]
    created = await _post(conn, skeleton, tenant)
    assert created["public_id"]
    assert created["author_id"] == tenant.user_public_id

    feed = await social.list_feed(conn, viewer_id=tenant.user_id, limit=10, cursor_keys={})
    assert created["public_id"] in {row["public_id"] for row in feed}

    updated = await social.update_post(
        conn,
        actor_user_id=tenant.user_id,
        public_id=created["public_id"],
        request_id="pytest",
        ip_address=None,
        changes={"content": "Shipping the billing engine tomorrow."},
    )
    assert "tomorrow" in updated["content"]

    await social.delete_post(
        conn,
        actor_user_id=tenant.user_id,
        public_id=created["public_id"],
        request_id="pytest",
        ip_address=None,
    )
    with pytest.raises(ResourceNotFoundError):
        await social.get_post(conn, viewer_id=tenant.user_id, public_id=created["public_id"])


async def test_post_authoring_is_enforced(conn, skeleton, tenants) -> None:
    from app.services import social

    admin, worker = tenants["admin"], tenants["worker"]
    created = await _post(conn, skeleton, admin)
    with pytest.raises(ResourceNotFoundError):
        await social.update_post(
            conn,
            actor_user_id=worker.user_id,
            public_id=created["public_id"],
            request_id="pytest",
            ip_address=None,
            changes={"content": "hijacked"},
        )
    with pytest.raises(ValidationError):
        await _post(conn, skeleton, admin, content="   ")
    with pytest.raises(ValidationError):
        await _post(conn, skeleton, admin, post_type="NOPE")


async def test_reaction_replaces_and_comment_threads(conn, skeleton, tenants) -> None:
    from app.services import social

    tenant = tenants["admin"]
    created = await _post(conn, skeleton, tenant)

    liked = await social.toggle_reaction(
        conn,
        actor_user_id=tenant.user_id,
        public_id=created["public_id"],
        reaction="LIKE",
        request_id="pytest",
    )
    assert liked["reaction_count"] == 1
    celebrated = await social.toggle_reaction(
        conn,
        actor_user_id=tenant.user_id,
        public_id=created["public_id"],
        reaction="CELEBRATE",
        request_id="pytest",
    )
    # One reaction per user: replace, never a second row.
    assert celebrated["reaction_count"] == 1

    comment = await social.add_comment(
        conn,
        actor_user_id=tenant.user_id,
        public_id=created["public_id"],
        request_id="pytest",
        ip_address=None,
        payload={"content": "Congrats!"},
    )
    assert comment["post_id"] == created["public_id"]
    comments = await social.list_comments(
        conn, viewer_id=tenant.user_id, public_id=created["public_id"], limit=10
    )
    assert [c["id"] for c in comments] == [comment["id"]]

    shared = await social.share_post(
        conn,
        actor_user_id=tenant.user_id,
        public_id=created["public_id"],
        request_id="pytest",
        ip_address=None,
        payload={"commentary": "Worth a read"},
    )
    assert shared["post_id"] == created["public_id"]


# =============================================================================
# connections
# =============================================================================
async def test_connection_lifecycle(conn, skeleton, tenants) -> None:
    from app.services import social

    admin, worker = tenants["admin"], tenants["worker"]
    sent = await social.send_request(
        conn,
        actor_user_id=admin.user_id,
        target_public_id=worker.user_public_id,
        request_id="pytest",
        message="Let's connect",
    )
    assert sent["state"] == "PENDING"

    accepted = await social.respond_request(
        conn,
        actor_user_id=worker.user_id,
        requester_public_id=admin.user_public_id,
        request_id="pytest",
        accept=True,
    )
    assert accepted["state"] == "ACCEPTED"

    directory = await social.list_connections(conn, viewer_id=admin.user_id, limit=10)
    assert worker.user_public_id in {row["public_id"] for row in directory}

    await social.remove_connection(
        conn,
        actor_user_id=admin.user_id,
        target_public_id=worker.user_public_id,
        request_id="pytest",
    )
    directory = await social.list_connections(conn, viewer_id=admin.user_id, limit=10)
    assert worker.user_public_id not in {row["public_id"] for row in directory}


async def test_blocked_users_cannot_connect_or_message(conn, skeleton, tenants) -> None:
    from app.services import messaging, social

    admin, worker = tenants["admin"], tenants["worker"]
    await social.block_user(
        conn,
        actor_user_id=admin.user_id,
        target_public_id=worker.user_public_id,
        request_id="pytest",
    )
    with pytest.raises(BusinessRuleViolationError) as caught:
        await social.send_request(
            conn,
            actor_user_id=admin.user_id,
            target_public_id=worker.user_public_id,
            request_id="pytest",
            message=None,
        )
    assert caught.value.details["reason"] == "USER_BLOCKED"
    with pytest.raises(BusinessRuleViolationError):
        await messaging.open_conversation(
            conn,
            actor_user_id=worker.user_id,
            target_public_id=admin.user_public_id,
            request_id="pytest",
        )


async def test_report_accepts_user_and_post_targets(conn, skeleton, tenants) -> None:
    from app.services import social

    admin, worker = tenants["admin"], tenants["worker"]
    created = await _post(conn, skeleton, admin)
    user_report = await social.report(
        conn,
        actor_user_id=admin.user_id,
        request_id="pytest",
        payload={"target_type": "USER", "target_id": worker.user_public_id, "reason": "spam"},
    )
    assert user_report["id"]
    post_report = await social.report(
        conn,
        actor_user_id=admin.user_id,
        request_id="pytest",
        payload={"target_type": "POST", "target_id": created["public_id"], "reason": "spam"},
    )
    assert post_report["id"]
    with pytest.raises(ValidationError):
        await social.report(
            conn,
            actor_user_id=admin.user_id,
            request_id="pytest",
            payload={"target_type": "PLANET", "target_id": "x", "reason": "spam"},
        )


# =============================================================================
# messaging
# =============================================================================
async def test_messaging_requires_connection(conn, skeleton, tenants) -> None:
    from app.services import messaging, social

    admin, worker = tenants["admin"], tenants["worker"]
    with pytest.raises(BusinessRuleViolationError) as caught:
        await messaging.open_conversation(
            conn,
            actor_user_id=admin.user_id,
            target_public_id=worker.user_public_id,
            request_id="pytest",
        )
    assert caught.value.details["reason"] == "NOT_CONNECTED"

    await social.send_request(
        conn,
        actor_user_id=admin.user_id,
        target_public_id=worker.user_public_id,
        request_id="pytest",
        message=None,
    )
    await social.respond_request(
        conn,
        actor_user_id=worker.user_id,
        requester_public_id=admin.user_public_id,
        request_id="pytest",
        accept=True,
    )

    thread = await messaging.open_conversation(
        conn,
        actor_user_id=admin.user_id,
        target_public_id=worker.user_public_id,
        request_id="pytest",
    )
    same = await messaging.open_conversation(
        conn,
        actor_user_id=worker.user_id,
        target_public_id=admin.user_public_id,
        request_id="pytest",
    )
    assert thread["public_id"] == same["public_id"]

    sent = await messaging.send_message(
        conn,
        actor_user_id=admin.user_id,
        conversation_public_id=thread["public_id"],
        request_id="pytest",
        ip_address=None,
        payload={"content": "Hello!"},
    )
    assert sent["id"]

    as_worker = await messaging.list_messages(
        conn,
        actor_user_id=worker.user_id,
        conversation_public_id=thread["public_id"],
        limit=50,
    )
    assert [m["id"] for m in as_worker] == [sent["id"]]

    found = await messaging.search_messages(
        conn, actor_user_id=worker.user_id, query="hell", limit=10
    )
    assert [m["id"] for m in found] == [sent["id"]]
    with pytest.raises(ValidationError):
        await messaging.search_messages(conn, actor_user_id=worker.user_id, query="x", limit=10)


# =============================================================================
# schedules
# =============================================================================
async def test_schedule_crud_and_run_due(conn, skeleton, tenants) -> None:
    from app.services import schedules

    tenant = tenants["admin"]
    with pytest.raises(ValidationError):
        await schedules.create_schedule(
            conn,
            company_id=tenant.company_id,
            actor_user_id=tenant.user_id,
            request_id="pytest",
            ip_address=None,
            payload={"amount": "0", "frequency": "WEEKLY", "next_run_date": "2026-01-01"},
        )
    with pytest.raises(ValidationError):
        await schedules.create_schedule(
            conn,
            company_id=tenant.company_id,
            actor_user_id=tenant.user_id,
            request_id="pytest",
            ip_address=None,
            payload={"amount": "100", "frequency": "YEARLY", "next_run_date": "2026-01-01"},
        )

    created = await schedules.create_schedule(
        conn,
        company_id=tenant.company_id,
        actor_user_id=tenant.user_id,
        request_id="pytest",
        ip_address=None,
        payload={
            "amount": "250.00",
            "currency": "USD",
            "frequency": "MONTHLY",
            "next_run_date": "2020-01-01",
        },
    )
    assert created["status"] == "ACTIVE"

    listed = await schedules.list_schedules(conn, company_id=tenant.company_id, limit=10)
    assert created["public_id"] in {row["public_id"] for row in listed}

    paused = await schedules.set_schedule_status(
        conn,
        company_id=tenant.company_id,
        public_id=created["public_id"],
        actor_user_id=tenant.user_id,
        request_id="pytest",
        status="PAUSED",
    )
    assert paused["status"] == "PAUSED"

    # A paused schedule produces no occurrences.
    ran = await schedules.run_due_schedules(
        conn,
        company_id=tenant.company_id,
        actor_user_id=tenant.user_id,
        request_id="pytest",
    )
    assert ran["created"] == 0


async def test_schedule_run_is_idempotent(conn, skeleton, tenants) -> None:
    from app.services import schedules

    tenant = tenants["admin"]
    created = await schedules.create_schedule(
        conn,
        company_id=tenant.company_id,
        actor_user_id=tenant.user_id,
        request_id="pytest",
        ip_address=None,
        payload={
            "amount": "100.00",
            "currency": "USD",
            "frequency": "WEEKLY",
            "next_run_date": "2020-01-01",
        },
    )
    from sqlalchemy import text as _text

    first = await schedules.run_due_schedules(
        conn,
        company_id=tenant.company_id,
        actor_user_id=tenant.user_id,
        request_id="pytest",
    )
    assert first["created"] == 1
    # Rewind to the same run date: the same occurrence must not pay twice.
    await conn.execute(
        _text(
            "UPDATE public.payment_schedules SET next_run_date = '2020-01-01'"
            " WHERE public_id = :pid"
        ),
        {"pid": created["public_id"]},
    )
    second = await schedules.run_due_schedules(
        conn,
        company_id=tenant.company_id,
        actor_user_id=tenant.user_id,
        request_id="pytest",
    )
    assert second["created"] == 0


# =============================================================================
# webhooks
# =============================================================================
def _stripe_signature(body: bytes, secret: str) -> str:
    stamp = "1759843200"
    digest = hmac.new(secret.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={stamp},v1={digest}"


async def test_webhook_rejects_and_dedupes(conn, skeleton, tenants, monkeypatch) -> None:

    from app.core.config import get_settings
    from app.core.errors import WebhookSignatureError
    from app.services import webhooks

    monkeypatch.setenv("PAYMENT_PROCESSOR_WEBHOOK_SECRET", "whsec-test-secret")
    get_settings.cache_clear()
    try:
        body = json.dumps({"id": "evt_test_1", "type": "payment.succeeded"}).encode()
        with pytest.raises(WebhookSignatureError):
            await webhooks.ingest_processor_webhook(
                conn,
                provider="stripe",
                raw_body=body,
                signature="t=1,v1=deadbeef",
                request_id="pytest",
            )
        with pytest.raises(ValidationError):
            await webhooks.ingest_processor_webhook(
                conn,
                provider="paypal",
                raw_body=body,
                signature="sha256=deadbeef",
                request_id="pytest",
            )

        good = _stripe_signature(body, "whsec-test-secret")
        first = await webhooks.ingest_processor_webhook(
            conn, provider="stripe", raw_body=body, signature=good, request_id="pytest"
        )
        assert first == {"ok": True, "duplicate": False, "request_id": "pytest"}
        second = await webhooks.ingest_processor_webhook(
            conn, provider="stripe", raw_body=body, signature=good, request_id="pytest"
        )
        assert second["duplicate"] is True
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


# =============================================================================
# company posts: actor vs publishing identity
# =============================================================================
async def test_company_post_carries_publisher_identity(conn, skeleton, tenants) -> None:
    from app.services import social

    tenant = tenants["admin"]  # SUPER_ADMIN holds posts.create
    created = await social.create_post(
        conn,
        actor_user_id=tenant.user_id,
        company_id=tenant.company_id,
        request_id="pytest",
        ip_address=None,
        payload={"content": "We are hiring engineers.", "as_company": True},
    )
    # The human is the actor; the company is the publishing identity.
    assert created["author_id"] == tenant.user_public_id
    assert created["company_public_id"] == tenant.company_public_id
    assert created["company_name"]

    reread = await social.get_post(conn, viewer_id=tenant.user_id, public_id=created["public_id"])
    assert reread["company_public_id"] == tenant.company_public_id


async def test_personal_post_carries_no_company_identity(conn, skeleton, tenants) -> None:
    tenant = tenants["admin"]
    # Even with a company context present, a personal post is personal.
    created = await _post(conn, skeleton, tenant)
    assert created["company_id"] is None
    assert created["company_public_id"] is None


async def test_company_post_requires_posts_create(conn, skeleton, tenants) -> None:
    import uuid

    from sqlalchemy import text

    from app.core.security import provision_user
    from app.services import companies as company_service
    from app.services import social

    admin = tenants["admin"]
    email = f"reader_{uuid.uuid4().hex[:8]}@t.test"
    user_id = uuid.UUID(
        str(
            await provision_user(
                conn, str(uuid.uuid4()), email=email, first_name="Reader", verified=True
            )
        )
    )
    await company_service.create_role(
        conn,
        company_id=admin.company_id,
        name="Reader Role",
        description="Reads posts, publishes nothing.",
        permission_keys=["posts.read"],
        actor_user_id=admin.user_id,
        request_id="pytest",
    )
    role_id = (
        await conn.execute(
            text(
                "SELECT id FROM public.company_roles "
                "WHERE company_id = :cid AND key = 'READER_ROLE'"
            ),
            {"cid": admin.company_id},
        )
    ).scalar_one()
    await conn.execute(
        text(
            "INSERT INTO public.company_memberships "
            "(company_id, user_id, role_id, status, joined_at) "
            "VALUES (:cid, :uid, :rid, 'ACTIVE', now())"
        ),
        {"cid": admin.company_id, "uid": user_id, "rid": role_id},
    )

    with pytest.raises(PermissionDeniedError):
        await social.create_post(
            conn,
            actor_user_id=user_id,
            company_id=admin.company_id,
            request_id="pytest",
            ip_address=None,
            payload={"content": "Corporate announcement.", "as_company": True},
        )


async def test_company_post_without_company_context_is_rejected(conn, skeleton, tenants) -> None:
    from app.services import social

    tenant = tenants["admin"]
    with pytest.raises(ValidationError):
        await social.create_post(
            conn,
            actor_user_id=tenant.user_id,
            company_id=None,
            request_id="pytest",
            ip_address=None,
            payload={"content": "Nowhere to publish.", "as_company": True},
        )


# =============================================================================
# connection requests: incoming, outgoing, accept, decline
# =============================================================================
async def test_connection_requests_split_by_direction(conn, skeleton, tenants) -> None:
    from app.services import social

    admin, worker = tenants["admin"], tenants["worker"]
    await social.send_request(
        conn,
        actor_user_id=admin.user_id,
        target_public_id=worker.user_public_id,
        request_id="pytest",
        message="Let's connect.",
    )
    incoming = await social.list_requests(conn, viewer_id=worker.user_id)
    assert [r["public_id"] for r in incoming["incoming"]] == [admin.user_public_id]
    assert incoming["outgoing"] == []

    outgoing = await social.list_requests(conn, viewer_id=admin.user_id)
    assert [r["public_id"] for r in outgoing["outgoing"]] == [worker.user_public_id]
    assert outgoing["incoming"] == []


async def test_connection_accept_and_decline(conn, skeleton, tenants) -> None:
    import uuid

    from sqlalchemy import text

    from app.core.security import provision_user
    from app.services import social

    admin = tenants["admin"]
    joiner_id = await provision_user(
        conn,
        str(uuid.uuid4()),
        email=f"joiner_{uuid.uuid4().hex[:8]}@t.test",
        first_name="Joiner",
        verified=True,
    )
    joiner_public_id = (
        await conn.execute(
            text("SELECT public_id FROM public.users WHERE id = :uid"),
            {"uid": joiner_id},
        )
    ).scalar_one()

    await social.send_request(
        conn,
        actor_user_id=admin.user_id,
        target_public_id=joiner_public_id,
        request_id="pytest",
        message=None,
    )
    await social.respond_request(
        conn,
        actor_user_id=uuid.UUID(str(joiner_id)),
        requester_public_id=admin.user_public_id,
        request_id="pytest",
        accept=True,
    )
    directory = await social.list_connections(conn, viewer_id=admin.user_id, limit=10)
    assert joiner_public_id in {row["public_id"] for row in directory}
    pending = await social.list_requests(conn, viewer_id=uuid.UUID(str(joiner_id)))
    assert pending["incoming"] == []

    stranger_id = await provision_user(
        conn,
        str(uuid.uuid4()),
        email=f"stranger_{uuid.uuid4().hex[:8]}@t.test",
        first_name="Stranger",
        verified=True,
    )
    stranger_public_id = (
        await conn.execute(
            text("SELECT public_id FROM public.users WHERE id = :uid"),
            {"uid": stranger_id},
        )
    ).scalar_one()
    await social.send_request(
        conn,
        actor_user_id=admin.user_id,
        target_public_id=stranger_public_id,
        request_id="pytest",
        message=None,
    )
    await social.respond_request(
        conn,
        actor_user_id=uuid.UUID(str(stranger_id)),
        requester_public_id=admin.user_public_id,
        request_id="pytest",
        accept=False,
    )
    directory = await social.list_connections(conn, viewer_id=admin.user_id, limit=10)
    assert str(stranger_public_id) not in {row["public_id"] for row in directory}
