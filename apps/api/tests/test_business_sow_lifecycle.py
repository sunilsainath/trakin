"""SOW acceptance lifecycle: submit, reject with reason, reopen, accept.

Rejection sets status REJECTED, which the sow_status enum did not contain
until migration 0029: every rejection failed with an enum violation and the
accept/reject flow in the product spec could never complete.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


async def _draft_sow(conn, skeleton, tenants) -> str:
    from sqlalchemy import text

    from app.services import code

    tenant = tenants["admin"]
    project_public_id = (
        await conn.execute(
            text("SELECT public_id FROM public.projects WHERE id = :pid"),
            {"pid": skeleton.project},
        )
    ).scalar_one()
    role_public_id = (
        await conn.execute(
            text("SELECT public_id FROM public.project_roles WHERE id = :rid"),
            {"rid": skeleton.project_role},
        )
    ).scalar_one()
    sow = await code.create_sow(
        conn,
        company_id=tenant.company_id,
        project_public_id=project_public_id,
        actor_user_id=tenant.user_id,
        request_id="pytest",
        ip_address=None,
        payload={
            "title": "Lifecycle SOW",
            "counterparty_company_id": tenants["worker"].company_public_id,
            "roles": [{"project_role_id": role_public_id, "quantity": 1}],
        },
    )
    assert sow["status"] == "DRAFT"
    return str(sow["public_id"])


async def _transition(conn, tenants, public_id: str, target: str, reason=None) -> dict:
    from app.services import code

    return await code.transition_sow(
        conn,
        company_id=tenants["admin"].company_id,
        public_id=public_id,
        target=target,
        actor_user_id=tenants["admin"].user_id,
        request_id="pytest",
        ip_address=None,
        reason=reason,
    )


async def _as_worker(conn, tenants) -> None:
    """Act as the counterparty: session identity follows the caller, as the
    API dependency sets it per request from the token + company header."""
    from app.db.session import set_identity

    await set_identity(
        conn,
        user_id=tenants["worker"].user_id,
        company_id=tenants["worker"].company_id,
        request_id="pytest",
    )


async def _as_admin(conn, tenants) -> None:
    from app.db.session import set_identity

    await set_identity(
        conn,
        user_id=tenants["admin"].user_id,
        company_id=tenants["admin"].company_id,
        request_id="pytest",
    )


async def test_sow_reject_reopen_accept_lifecycle(conn, skeleton, tenants) -> None:
    from app.services import code

    worker = tenants["worker"]
    public_id = await _draft_sow(conn, skeleton, tenants)

    sent = await _transition(conn, tenants, public_id, "PENDING_APPROVAL")
    assert sent["status"] == "PENDING_APPROVAL"

    await _as_worker(conn, tenants)
    rejected = await code.reject_sow(
        conn,
        company_id=worker.company_id,
        user_id=worker.user_id,
        public_id=public_id,
        actor_user_id=worker.user_id,
        request_id="pytest",
        ip_address=None,
        reason="Rate too high.",
        notes="Please revise the rate card.",
    )
    assert rejected["status"] == "REJECTED"
    assert rejected["reject_reason"] == "Rate too high."

    await _as_admin(conn, tenants)
    reopened = await _transition(conn, tenants, public_id, "DRAFT")
    assert reopened["status"] == "DRAFT"

    await _transition(conn, tenants, public_id, "PENDING_APPROVAL")
    await _as_worker(conn, tenants)
    accepted = await code.accept_sow(
        conn,
        company_id=worker.company_id,
        user_id=worker.user_id,
        public_id=public_id,
        actor_user_id=worker.user_id,
        request_id="pytest",
        ip_address=None,
    )
    assert accepted["status"] == "ACTIVE"


async def test_sow_reject_requires_a_reason(conn, skeleton, tenants) -> None:
    from app.core.errors import ValidationError
    from app.services import code

    worker = tenants["worker"]
    public_id = await _draft_sow(conn, skeleton, tenants)
    await _transition(conn, tenants, public_id, "PENDING_APPROVAL")

    await _as_worker(conn, tenants)
    with pytest.raises(ValidationError):
        await code.reject_sow(
            conn,
            company_id=worker.company_id,
            user_id=worker.user_id,
            public_id=public_id,
            actor_user_id=worker.user_id,
            request_id="pytest",
            ip_address=None,
            reason="  ",
        )


async def test_accept_generates_one_draft_contract_per_role(conn, skeleton, tenants) -> None:
    from sqlalchemy import text

    from app.services import code

    admin = tenants["admin"]
    worker = tenants["worker"]

    project_public_id = (
        await conn.execute(
            text("SELECT public_id FROM public.projects WHERE id = :pid"),
            {"pid": skeleton.project},
        )
    ).scalar_one()
    role_one = (
        await conn.execute(
            text("SELECT public_id FROM public.project_roles WHERE id = :rid"),
            {"rid": skeleton.project_role},
        )
    ).scalar_one()
    role_two = await code.create_project_role(
        conn,
        company_id=admin.company_id,
        project_public_id=project_public_id,
        actor_user_id=admin.user_id,
        request_id="pytest",
        ip_address=None,
        payload={"title": "Second role", "required_count": 2},
    )
    sow = await code.create_sow(
        conn,
        company_id=admin.company_id,
        project_public_id=project_public_id,
        actor_user_id=admin.user_id,
        request_id="pytest",
        ip_address=None,
        payload={
            "title": "Auto SOW",
            "counterparty_company_id": worker.company_public_id,
            "roles": [
                {"project_role_id": role_one, "quantity": 1},
                {"project_role_id": role_two["public_id"], "quantity": 2},
            ],
        },
    )
    public_id = str(sow["public_id"])

    await _transition(conn, tenants, public_id, "PENDING_APPROVAL")
    await _as_worker(conn, tenants)
    rejected = await code.reject_sow(
        conn,
        company_id=worker.company_id,
        user_id=worker.user_id,
        public_id=public_id,
        actor_user_id=worker.user_id,
        request_id="pytest",
        ip_address=None,
        reason="Revise quantities.",
    )
    assert rejected["status"] == "REJECTED"

    # Reopen, resubmit and accept: exactly one draft contract per role, even
    # across the repeated cycle.
    await _as_admin(conn, tenants)
    await _transition(conn, tenants, public_id, "DRAFT")
    await _transition(conn, tenants, public_id, "PENDING_APPROVAL")
    await _as_worker(conn, tenants)
    await code.accept_sow(
        conn,
        company_id=worker.company_id,
        user_id=worker.user_id,
        public_id=public_id,
        actor_user_id=worker.user_id,
        request_id="pytest",
        ip_address=None,
    )

    contracts = (
        (
            await conn.execute(
                text(
                    "SELECT public_id, status FROM public.contracts "
                    "WHERE sow_id = (SELECT id FROM public.sows WHERE public_id = :pid) "
                    "AND deleted_at IS NULL ORDER BY public_id"
                ),
                {"pid": public_id},
            )
        )
        .mappings()
        .all()
    )
    assert len(contracts) == 2
    assert {c["status"] for c in contracts} == {"DRAFT"}

    # Regeneration is idempotent per role: roles that already have a contract
    # are skipped.
    from app.services import contracts as contract_service

    await _as_admin(conn, tenants)
    again = await contract_service.generate_contracts_from_sow(
        conn,
        company_id=admin.company_id,
        sow_public_id=public_id,
        actor_user_id=admin.user_id,
        request_id="pytest",
        ip_address=None,
    )
    assert again == []
    count = (
        await conn.execute(
            text(
                "SELECT count(*) FROM public.contracts "
                "WHERE sow_id = (SELECT id FROM public.sows WHERE public_id = :pid) "
                "AND deleted_at IS NULL"
            ),
            {"pid": public_id},
        )
    ).scalar_one()
    assert count == 2


async def test_contract_approval_chain_resolves_and_enforces_membership(
    conn, skeleton, tenants
) -> None:
    from sqlalchemy import text

    from app.core.errors import BusinessRuleViolationError
    from app.services import contracts as contract_service

    admin = tenants["admin"]
    project_public_id = (
        await conn.execute(
            text("SELECT public_id FROM public.projects WHERE id = :pid"),
            {"pid": skeleton.project},
        )
    ).scalar_one()
    sow_public_id = (
        await conn.execute(
            text("SELECT public_id FROM public.sows WHERE id = :sid"),
            {"sid": skeleton.sow},
        )
    ).scalar_one()

    chain = [
        {
            "user_public_id": admin.user_public_id,
            "required_permission": "timesheets.approve",
            "due_within_days": 5,
        },
        {"user_public_id": tenants["worker"].user_public_id},
    ]
    contract = await contract_service.create_contract(
        conn,
        company_id=admin.company_id,
        actor_user_id=admin.user_id,
        request_id="pytest",
        ip_address=None,
        payload={
            "project_id": project_public_id,
            "sow_id": sow_public_id,
            "title": "Chained contract",
            "timesheet_approval_chain": chain,
        },
    )
    stored = contract["timesheet_approval_chain"]
    assert [s["required_permission"] for s in stored["steps"]] == [
        "timesheets.approve",
        "timesheets.approve",
    ]
    assert stored["steps"][0]["user_public_id"] == admin.user_public_id
    assert stored["steps"][1]["due_within_days"] == 3

    with pytest.raises(BusinessRuleViolationError):
        await contract_service.create_contract(
            conn,
            company_id=admin.company_id,
            actor_user_id=admin.user_id,
            request_id="pytest",
            ip_address=None,
            payload={
                "project_id": project_public_id,
                "sow_id": sow_public_id,
                "title": "Stranger chain",
                "timesheet_approval_chain": [{"user_public_id": "U00000000"}],
            },
        )

    updated = await contract_service.update_contract(
        conn,
        company_id=admin.company_id,
        public_id=str(contract["public_id"]),
        actor_user_id=admin.user_id,
        request_id="pytest",
        ip_address=None,
        changes={"timesheet_approval_chain": [chain[0]]},
    )
    assert len(updated["timesheet_approval_chain"]["steps"]) == 1
