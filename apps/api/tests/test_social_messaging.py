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
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from app.core.errors import BusinessRuleViolationError, ResourceNotFoundError, ValidationError

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
# connection suggestions
# =============================================================================
async def _stranger(conn, tag: str) -> dict[str, Any]:
    """A user outside every fixture company, for graph tests."""
    from app.core.security import provision_user

    user_id = uuid.UUID(
        await provision_user(
            conn,
            str(uuid.uuid4()),
            email=f"suggest_{tag}_{uuid.uuid4().hex[:6]}@example.test",
            first_name=tag.title(),
            verified=True,
        )
    )
    row = (
        (
            await conn.execute(
                text("SELECT public_id FROM public.users WHERE id = :uid"),
                {"uid": user_id},
            )
        )
        .mappings()
        .first()
    )
    assert row is not None
    return {"user_id": user_id, "user_public_id": str(row["public_id"])}


async def _connect(conn, a: Any, b: Any) -> None:
    """An ACCEPTED connection between two test users."""
    from app.services import social

    def _uid(u: Any) -> uuid.UUID:
        return u.user_id if hasattr(u, "user_id") else u["user_id"]

    def _pid(u: Any) -> str:
        return u.user_public_id if hasattr(u, "user_public_id") else u["user_public_id"]

    await social.send_request(
        conn,
        actor_user_id=_uid(a),
        target_public_id=_pid(b),
        request_id="pytest",
        message=None,
    )
    await social.respond_request(
        conn,
        actor_user_id=_uid(b),
        requester_public_id=_pid(a),
        request_id="pytest",
        accept=True,
    )


async def test_suggestions_surface_mutual_connections(conn, skeleton, tenants) -> None:
    """Friends-of-friends appear with mutual counts; ties and self do not."""
    _ = skeleton
    from app.services import social

    admin, worker = tenants["admin"], tenants["worker"]
    stranger = await _stranger(conn, "far")
    await _connect(conn, admin, worker)
    await _connect(conn, worker, stranger)

    suggestions = await social.suggestion_lists(conn, viewer_id=admin.user_id)
    people = {p["user_public_id"]: p for p in suggestions["people"]}
    assert stranger["user_public_id"] in people
    assert people[stranger["user_public_id"]]["mutual_count"] == 1
    assert people[stranger["user_public_id"]]["display_name"] == "Far"
    assert worker.user_public_id not in people
    assert admin.user_public_id not in people

    from_stranger = await social.suggestion_lists(conn, viewer_id=stranger["user_id"])
    assert admin.user_public_id in {p["user_public_id"] for p in from_stranger["people"]}


async def test_suggestions_exclude_pending_blocked_and_unconnected(conn, skeleton, tenants) -> None:
    """Pending requests, blocks and zero-mutual strangers are never suggested."""
    _ = skeleton
    from app.services import social

    admin, worker = tenants["admin"], tenants["worker"]
    pending = await _stranger(conn, "pending")
    blocked = await _stranger(conn, "blocked")
    lone = await _stranger(conn, "lone")
    await _connect(conn, admin, worker)

    await social.send_request(
        conn,
        actor_user_id=admin.user_id,
        target_public_id=pending["user_public_id"],
        request_id="pytest",
        message=None,
    )
    await social.block_user(
        conn,
        actor_user_id=admin.user_id,
        target_public_id=blocked["user_public_id"],
        request_id="pytest",
    )

    people = {
        p["user_public_id"]
        for p in (await social.suggestion_lists(conn, viewer_id=admin.user_id))["people"]
    }
    assert pending["user_public_id"] not in people
    assert blocked["user_public_id"] not in people
    assert lone["user_public_id"] not in people


async def test_suggestions_companies_exclude_own(conn, skeleton, tenants) -> None:
    """Employers of connections surface; the viewer's own companies never do."""
    _ = skeleton
    from app.services import social

    admin, worker = tenants["admin"], tenants["worker"]
    stranger = await _stranger(conn, "hired")
    await _connect(conn, admin, worker)
    await _connect(conn, worker, stranger)

    role_id = (
        await conn.execute(
            text(
                "SELECT id::text FROM public.company_roles"
                " WHERE company_id = :cid AND key = 'SUPER_ADMIN'"
            ),
            {"cid": worker.company_id},
        )
    ).scalar_one()
    await conn.execute(
        text(
            """
            INSERT INTO public.company_memberships
              (company_id, user_id, role_id, status, joined_at)
            VALUES (CAST(:cid AS uuid), CAST(:uid AS uuid), CAST(:rid AS uuid),
                    'ACTIVE', now())
            """
        ),
        {"cid": worker.company_id, "uid": stranger["user_id"], "rid": role_id},
    )

    companies = (await social.suggestion_lists(conn, viewer_id=admin.user_id))["companies"]
    by_id = {c["company_public_id"]: c for c in companies}
    assert worker.company_public_id in by_id
    assert by_id[worker.company_public_id]["connections_count"] >= 1
    assert admin.company_public_id not in by_id


# =============================================================================
# company-less access (hard rule: individuals are first-class users)
# =============================================================================
async def test_company_less_user_can_post_and_read_feed(conn, tenants) -> None:
    """No company, no header: posting and reading still work user-scoped."""
    _ = tenants
    from app.services import social

    stranger = await _stranger(conn, "solo")
    created = await social.create_post(
        conn,
        actor_user_id=stranger["user_id"],
        company_id=None,
        request_id="pytest",
        ip_address=None,
        payload={"content": "Posting without a company.", "visibility": "PUBLIC"},
    )
    assert created["public_id"]
    feed = await social.list_feed(conn, viewer_id=stranger["user_id"], limit=10, cursor_keys={})
    assert created["public_id"] in {row["public_id"] for row in feed}


async def test_company_less_users_can_connect_and_message(conn, tenants) -> None:
    """The full social loop runs on identity alone, never on membership."""
    _ = tenants
    from app.services import messaging, social

    first = await _stranger(conn, "first")
    second = await _stranger(conn, "second")
    await social.send_request(
        conn,
        actor_user_id=first["user_id"],
        target_public_id=second["user_public_id"],
        request_id="pytest",
        message=None,
    )
    await social.respond_request(
        conn,
        actor_user_id=second["user_id"],
        requester_public_id=first["user_public_id"],
        request_id="pytest",
        accept=True,
    )
    thread = await messaging.open_conversation(
        conn,
        actor_user_id=first["user_id"],
        target_public_id=second["user_public_id"],
        request_id="pytest",
    )
    assert thread["public_id"]
    sent = await messaging.send_message(
        conn,
        actor_user_id=first["user_id"],
        conversation_public_id=thread["public_id"],
        request_id="pytest",
        ip_address=None,
        payload={"content": "Hello from outside any company."},
    )
    assert sent["id"]
    thread_messages = await messaging.list_messages(
        conn,
        actor_user_id=second["user_id"],
        conversation_public_id=thread["public_id"],
        limit=10,
    )
    assert sent["id"] in {row["id"] for row in thread_messages}
    inbox = await messaging.list_conversations(conn, actor_user_id=second["user_id"], limit=10)
    assert thread["public_id"] in {row["public_id"] for row in inbox}


async def test_user_or_permission_dep_branches_correctly(conn, tenants) -> None:
    """No company: pass. Company without the key: 403. With it: pass."""
    from app.api.deps import RequestContext, require_user_or_permission
    from app.core.errors import PermissionDeniedError
    from app.core.security import AuthenticatedUser

    _ = tenants
    auth = AuthenticatedUser(
        auth_id=str(uuid.uuid4()),
        email="nobody@example.test",
        email_verified=True,
        session_id=None,
        provider="test",
    )
    dep = require_user_or_permission("posts.read")

    bare = RequestContext(user_id=uuid.uuid4(), auth=auth, request_id="pytest")
    assert await dep((bare, conn)) == (bare, conn)

    denied = RequestContext(
        user_id=uuid.uuid4(),
        auth=auth,
        company_id=uuid.uuid4(),
        request_id="pytest",
        permissions=frozenset({"other.key"}),
    )
    with pytest.raises(PermissionDeniedError):
        await dep((denied, conn))

    allowed = RequestContext(
        user_id=uuid.uuid4(),
        auth=auth,
        company_id=uuid.uuid4(),
        request_id="pytest",
        permissions=frozenset({"posts.read"}),
        role_keys=("MEMBER",),
    )
    assert await dep((allowed, conn)) == (allowed, conn)
