"""Company invitation lifecycle: invite, preview, accept, expiry, misuse.

The invitation token is the credential: only its sha256 is stored, it is
single-use, it expires, and it can only be redeemed by the user whose email
matches the invited address. These tests prove that contract at the service
layer, inside the rolled-back `conn` transaction like every other
database-backed test.
"""

from __future__ import annotations

import hashlib
import uuid

import pytest
from sqlalchemy import text

from app.core.errors import (
    BusinessRuleViolationError,
    PermissionDeniedError,
    ResourceNotFoundError,
)
from app.core.security import provision_user
from app.services import companies as company_service

pytestmark = pytest.mark.integration


async def _provision(conn, email: str) -> uuid.UUID:
    user_id = await provision_user(
        conn,
        str(uuid.uuid4()),
        email=email,
        first_name="Invitee",
        verified=True,
    )
    return uuid.UUID(str(user_id))


async def _invite(conn, tenants, email: str) -> dict:
    admin = tenants["admin"]
    return await company_service.invite_member(
        conn,
        company_id=admin.company_id,
        email=email,
        role_key="COMPANY_ADMIN",
        job_title="Engineer",
        message=None,
        actor_user_id=admin.user_id,
        request_id="pytest",
    )


async def test_invite_returns_single_use_token_and_pending_preview(conn, tenants) -> None:
    email = f"invited_{uuid.uuid4().hex[:8]}@t.test"
    created = await _invite(conn, tenants, email)

    assert created["invitation_token"]
    assert created["public_id"]

    # Only the hash is stored: a database leak does not yield the token.
    stored = (
        (await conn.execute(text("SELECT token_hash FROM public.company_invitations")))
        .mappings()
        .all()
    )
    expected = hashlib.sha256(created["invitation_token"].encode()).hexdigest()
    assert [r["token_hash"] for r in stored] == [expected]

    preview = await company_service.preview_invitation(conn, token=created["invitation_token"])
    assert preview["email"] == email
    assert preview["status"] == "PENDING"
    assert preview["company_public_id"] == tenants["admin"].company_public_id
    assert preview["role_key"] == "COMPANY_ADMIN"


async def test_accept_creates_active_membership_and_consumes_token(conn, tenants) -> None:
    email = f"joiner_{uuid.uuid4().hex[:8]}@t.test"
    user_id = await _provision(conn, email)
    created = await _invite(conn, tenants, email)

    accepted = await company_service.accept_invitation(
        conn, token=created["invitation_token"], user_id=user_id, request_id="pytest"
    )
    assert accepted["company_public_id"] == tenants["admin"].company_public_id

    status = (
        await conn.execute(
            text(
                "SELECT status FROM public.company_memberships "
                "WHERE company_id = :cid AND user_id = :uid"
            ),
            {"cid": tenants["admin"].company_id, "uid": user_id},
        )
    ).scalar_one()
    assert status == "ACTIVE"

    invitation_status = (
        await conn.execute(
            text("SELECT status FROM public.company_invitations WHERE public_id = :pid"),
            {"pid": created["public_id"]},
        )
    ).scalar_one()
    assert invitation_status == "ACCEPTED"

    with pytest.raises(ResourceNotFoundError):
        await company_service.accept_invitation(
            conn, token=created["invitation_token"], user_id=user_id, request_id="pytest"
        )


async def test_accept_rejects_email_mismatch(conn, tenants) -> None:
    invited_email = f"invited_{uuid.uuid4().hex[:8]}@t.test"
    other_email = f"stranger_{uuid.uuid4().hex[:8]}@t.test"
    other_id = await _provision(conn, other_email)
    created = await _invite(conn, tenants, invited_email)

    with pytest.raises(PermissionDeniedError):
        await company_service.accept_invitation(
            conn, token=created["invitation_token"], user_id=other_id, request_id="pytest"
        )


async def test_accept_rejects_expired_token_and_marks_it(conn, tenants) -> None:
    email = f"late_{uuid.uuid4().hex[:8]}@t.test"
    user_id = await _provision(conn, email)
    created = await _invite(conn, tenants, email)

    await conn.execute(
        text(
            "UPDATE public.company_invitations SET expires_at = now() - interval '1 day' "
            "WHERE public_id = :pid"
        ),
        {"pid": created["public_id"]},
    )

    with pytest.raises(BusinessRuleViolationError):
        await company_service.accept_invitation(
            conn, token=created["invitation_token"], user_id=user_id, request_id="pytest"
        )

    status = (
        await conn.execute(
            text("SELECT status FROM public.company_invitations WHERE public_id = :pid"),
            {"pid": created["public_id"]},
        )
    ).scalar_one()
    assert status == "EXPIRED"


async def test_preview_rejects_unknown_token(conn, tenants) -> None:
    del tenants  # preview needs no fixture state beyond the database
    with pytest.raises(ResourceNotFoundError):
        await company_service.preview_invitation(conn, token="not-a-real-token")
