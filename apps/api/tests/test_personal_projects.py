"""CODE Phases 1-3: personal projects/SOWs/contracts + machines + acceptance.

Proves §5 (any registered user creates individual projects with no company),
§6-§7 (roles with server-side capacity), §10 (project transitions + read-only
terminal states), §12-§15 (personal SOWs with roles, counterparty
acceptance, rejection capture), and §17-§19/§24-§26 (personal contracts,
counterparty acceptance, responded-by capture).


Proves §5 (any registered user creates individual projects with no company),
§6-§7 (roles with server-side capacity), and §10 (status machine + read-only
terminal states) — for both company and personal projects.

Marked `integration`: needs the real database.

Run:
    pytest apps/api/tests/test_personal_projects.py -v
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.core.errors import (
    BusinessRuleViolationError,
    InvalidStateTransitionError,
    ResourceNotFoundError,
    ValidationError,
)

pytestmark = pytest.mark.integration


async def _personal(conn, user_id, **overrides):
    from app.services import code

    payload = {"name": "Solo website build", "project_type": "SERVICE"}
    payload.update(overrides)
    return await code.create_personal_project(
        conn,
        actor_user_id=user_id,
        request_id="pytest",
        ip_address=None,
        payload=payload,
    )


async def test_personal_project_lifecycle_without_company(conn, tenants) -> None:
    from app.services import code

    user_id = tenants["admin"].user_id
    created = await _personal(conn, user_id)
    assert created["public_id"].startswith("P")
    assert created["company_id"] is None
    assert created["category"] == "INDIVIDUAL"
    assert created["status"] == "DRAFT"

    listed = await code.list_personal_projects(
        conn, user_id=user_id, search=None, status=None, limit=10
    )
    assert created["public_id"] in {row["public_id"] for row in listed}

    # Company projects never leak into the personal list.
    assert all(row["company_id"] is None for row in listed)

    # A different user sees nothing (RLS + service scoping agree).
    other = await code.list_personal_projects(
        conn, user_id=tenants["worker"].user_id, search=None, status=None, limit=10
    )
    assert created["public_id"] not in {row["public_id"] for row in other}
    with pytest.raises(ResourceNotFoundError):
        await code.get_personal_project(
            conn, user_id=tenants["worker"].user_id, public_id=created["public_id"]
        )


async def test_personal_project_rejects_company_concepts(conn, tenants) -> None:
    user_id = tenants["admin"].user_id
    with pytest.raises(ValidationError):
        await _personal(conn, user_id, counterparty_company_id="COAAAAAAAA")
    with pytest.raises(ValidationError):
        await _personal(conn, user_id, owner_user_id=tenants["admin"].user_public_id)


async def test_personal_role_capacity_is_server_side(conn, tenants) -> None:
    from app.services import code

    user_id = tenants["admin"].user_id
    project = await _personal(conn, user_id)
    role = await code.create_personal_project_role(
        conn,
        user_id=user_id,
        project_public_id=project["public_id"],
        actor_user_id=user_id,
        request_id="pytest",
        ip_address=None,
        payload={"title": "Designer", "required_count": 2},
    )
    assert role["public_id"].startswith("R")

    roles = await code.list_personal_project_roles(
        conn, user_id=user_id, project_public_id=project["public_id"], limit=10
    )
    assert [r["public_id"] for r in roles] == [role["public_id"]]

    # Roles on another user's project are unreachable.
    with pytest.raises(ResourceNotFoundError):
        await code.create_personal_project_role(
            conn,
            user_id=tenants["worker"].user_id,
            project_public_id=project["public_id"],
            actor_user_id=tenants["worker"].user_id,
            request_id="pytest",
            ip_address=None,
            payload={"title": "Sneaky", "required_count": 1},
        )


async def test_project_status_machine_and_terminal_read_only(conn, skeleton, tenants) -> None:
    from app.services import code

    tenant = tenants["admin"]
    public_id = str(
        (
            await conn.execute(
                text("SELECT public_id FROM public.projects WHERE id = :i"),
                {"i": skeleton.project},
            )
        ).scalar_one()
    )

    # DRAFT -> ACTIVE is legal.
    project = await code.update_project(
        conn,
        company_id=tenant.company_id,
        public_id=public_id,
        actor_user_id=tenant.user_id,
        request_id="pytest",
        ip_address=None,
        changes={"status": "ACTIVE"},
    )
    assert project["status"] == "ACTIVE"

    # ACTIVE -> DRAFT is not.
    with pytest.raises(InvalidStateTransitionError) as caught:
        await code.update_project(
            conn,
            company_id=tenant.company_id,
            public_id=public_id,
            actor_user_id=tenant.user_id,
            request_id="pytest",
            ip_address=None,
            changes={"status": "DRAFT"},
        )
    assert caught.value.details["from"] == "ACTIVE"

    # ACTIVE -> COMPLETED, then the terminal state is read-only.
    done = await code.update_project(
        conn,
        company_id=tenant.company_id,
        public_id=public_id,
        actor_user_id=tenant.user_id,
        request_id="pytest",
        ip_address=None,
        changes={"status": "COMPLETED"},
    )
    assert done["status"] == "COMPLETED"
    with pytest.raises(InvalidStateTransitionError):
        await code.update_project(
            conn,
            company_id=tenant.company_id,
            public_id=public_id,
            actor_user_id=tenant.user_id,
            request_id="pytest",
            ip_address=None,
            changes={"name": "Edited after completion"},
        )


async def test_personal_project_status_machine(conn, tenants) -> None:
    from app.services import code

    user_id = tenants["admin"].user_id
    created = await _personal(conn, user_id)
    with pytest.raises(InvalidStateTransitionError):
        await code.update_personal_project(
            conn,
            user_id=user_id,
            public_id=created["public_id"],
            actor_user_id=user_id,
            request_id="pytest",
            ip_address=None,
            changes={"status": "COMPLETED"},
        )
    active = await code.update_personal_project(
        conn,
        user_id=user_id,
        public_id=created["public_id"],
        actor_user_id=user_id,
        request_id="pytest",
        ip_address=None,
        changes={"status": "ACTIVE"},
    )
    assert active["status"] == "ACTIVE"


async def test_personal_project_requires_a_real_user(conn, tenants) -> None:
    import uuid

    from app.services import code

    stranger = uuid.uuid4()
    created = await _personal(conn, tenants["admin"].user_id)
    # Unknown to every company and owner of nothing: RLS hides the row.
    with pytest.raises(ResourceNotFoundError):
        await code.get_personal_project(conn, user_id=stranger, public_id=created["public_id"])


async def _personal_sow(conn, user_id, project_public_id, counterparty=None, **overrides):
    from app.services import code

    payload = {"title": "Solo SOW", "sow_type": "INDIVIDUAL"}
    if counterparty is not None:
        payload["counterparty_user_id"] = counterparty
    payload.update(overrides)
    return await code.create_personal_sow(
        conn,
        user_id=user_id,
        project_public_id=project_public_id,
        actor_user_id=user_id,
        request_id="pytest",
        ip_address=None,
        payload=payload,
    )


async def test_personal_sow_lifecycle_with_roles(conn, tenants) -> None:
    from app.services import code

    user_id = tenants["admin"].user_id
    project = await _personal(conn, user_id)
    role = await code.create_personal_project_role(
        conn,
        user_id=user_id,
        project_public_id=project["public_id"],
        actor_user_id=user_id,
        request_id="pytest",
        ip_address=None,
        payload={"title": "Designer", "required_count": 3},
    )

    sow = await _personal_sow(
        conn,
        user_id,
        project["public_id"],
        counterparty=tenants["worker"].user_public_id,
        roles=[{"project_role_id": role["public_id"], "quantity": 2}],
    )
    assert sow["public_id"].startswith("S")
    assert sow["status"] == "DRAFT"
    assert [r["project_role_id"] for r in sow["roles"]] == [role["public_id"]]

    listed = await code.list_personal_sows(
        conn, user_id=user_id, project_public_id=None, status=None, limit=10
    )
    assert sow["public_id"] in {row["public_id"] for row in listed}

    # Another user's SOW is invisible.
    with pytest.raises(ResourceNotFoundError):
        await code.get_personal_sow(
            conn, user_id=tenants["worker"].user_id, public_id=sow["public_id"]
        )


async def test_company_sow_accept_and_reject_capture(conn, skeleton, tenants, act_as) -> None:
    from sqlalchemy import text

    from app.services import code

    tenant = tenants["admin"]
    draft = (
        (
            await conn.execute(
                text(
                    "INSERT INTO public.sows (project_id, company_id, sow_type,"
                    " counterparty_company_id, title, status)"
                    " VALUES (:p, :c, 'COMPANY', :cp, 'Decision SOW', 'DRAFT')"
                    " RETURNING public_id"
                ),
                {
                    "p": skeleton.project,
                    "c": tenant.company_id,
                    "cp": tenants["worker"].company_id,
                },
            )
        )
        .mappings()
        .one()
    )
    public_id = str(draft["public_id"])

    # DRAFT cannot be accepted or rejected: only submitted SOWs decide.
    with pytest.raises(InvalidStateTransitionError):
        await code.accept_sow(
            conn,
            company_id=tenant.company_id,
            user_id=tenant.user_id,
            public_id=public_id,
            actor_user_id=tenant.user_id,
            request_id="pytest",
            ip_address=None,
        )
    await code.transition_sow(
        conn,
        company_id=tenant.company_id,
        public_id=public_id,
        target="PENDING_APPROVAL",
        actor_user_id=tenant.user_id,
        request_id="pytest",
        ip_address=None,
    )

    # The counterparty company accepts: membership there is sufficient, no
    # role in the owning company required. Runs under the worker's session so
    # the membership check evaluates the right user.
    await act_as(tenants["worker"].user_id, tenants["worker"].company_id)
    accepted = await code.accept_sow(
        conn,
        company_id=tenants["worker"].company_id,
        user_id=tenants["worker"].user_id,
        public_id=public_id,
        actor_user_id=tenants["worker"].user_id,
        request_id="pytest",
        ip_address=None,
    )
    assert accepted["status"] == "ACTIVE"
    assert accepted["approved_by"] is not None

    # A stranger's decision is a 404, not a 403 (no existence oracle).
    import uuid

    from app.core.security import provision_user

    stranger_id = uuid.UUID(
        await provision_user(
            conn,
            str(uuid.uuid4()),
            email="stranger@svc-fixture.test",
            first_name="Stranger",
            verified=True,
        )
    )
    second = (
        (
            await conn.execute(
                text(
                    "INSERT INTO public.sows (project_id, company_id, sow_type,"
                    " counterparty_company_id, title, status)"
                    " VALUES (:p, :c, 'COMPANY', :cp, 'Stranger SOW', 'DRAFT')"
                    " RETURNING public_id"
                ),
                {
                    "p": skeleton.project,
                    "c": tenant.company_id,
                    "cp": tenants["worker"].company_id,
                },
            )
        )
        .mappings()
        .one()
    )
    await code.transition_sow(
        conn,
        company_id=tenant.company_id,
        public_id=str(second["public_id"]),
        target="PENDING_APPROVAL",
        actor_user_id=tenant.user_id,
        request_id="pytest",
        ip_address=None,
    )
    # The session still carries the worker identity from the accept above;
    # re-point it at the stranger so membership evaluates for the right user.
    await act_as(stranger_id, tenants["admin"].company_id)
    with pytest.raises(ResourceNotFoundError):
        await code.reject_sow(
            conn,
            company_id=None,
            user_id=stranger_id,
            public_id=str(second["public_id"]),
            actor_user_id=stranger_id,
            request_id="pytest",
            ip_address=None,
            reason="nope",
            notes=None,
        )


async def test_company_sow_rejection_keeps_evidence(conn, skeleton, tenants) -> None:
    from sqlalchemy import text

    from app.services import code

    tenant = tenants["admin"]
    draft = (
        (
            await conn.execute(
                text(
                    "INSERT INTO public.sows (project_id, company_id, sow_type,"
                    " counterparty_company_id, title, status)"
                    " VALUES (:p, :c, 'COMPANY', :cp, 'Rejected SOW', 'DRAFT')"
                    " RETURNING public_id"
                ),
                {
                    "p": skeleton.project,
                    "c": tenant.company_id,
                    "cp": tenants["worker"].company_id,
                },
            )
        )
        .mappings()
        .one()
    )
    public_id = str(draft["public_id"])
    await code.transition_sow(
        conn,
        company_id=tenant.company_id,
        public_id=public_id,
        target="PENDING_APPROVAL",
        actor_user_id=tenant.user_id,
        request_id="pytest",
        ip_address=None,
    )

    # Reason is mandatory.
    with pytest.raises(ValidationError):
        await code.reject_sow(
            conn,
            company_id=tenant.company_id,
            user_id=tenant.user_id,
            public_id=public_id,
            actor_user_id=tenant.user_id,
            request_id="pytest",
            ip_address=None,
            reason="  ",
            notes=None,
        )

    rejected = await code.reject_sow(
        conn,
        company_id=tenant.company_id,
        user_id=tenant.user_id,
        public_id=public_id,
        actor_user_id=tenant.user_id,
        request_id="pytest",
        ip_address=None,
        reason="Rate too high",
        notes="Revisit next quarter",
    )
    assert rejected["status"] == "REJECTED"
    assert rejected["reject_reason"] == "Rate too high"
    assert rejected["rejected_by"] is not None
    assert rejected["rejected_at"] is not None

    # Rejected SOWs are kept and can return to draft.
    reopened = await code.transition_sow(
        conn,
        company_id=tenant.company_id,
        public_id=public_id,
        target="DRAFT",
        actor_user_id=tenant.user_id,
        request_id="pytest",
        ip_address=None,
    )
    assert reopened["status"] == "DRAFT"


async def test_cross_company_contract_acceptance(conn, skeleton, tenants, act_as) -> None:
    """A counterparty member accepts a SENT contract (§26). Previously 404."""
    from sqlalchemy import text

    from app.services import contracts

    tenant = tenants["admin"]
    row = (
        (
            await conn.execute(
                text(
                    "INSERT INTO public.contracts (sow_id, project_id, company_id,"
                    " contract_type, counterparty_company_id, title, status)"
                    " VALUES (:s, :p, :c, 'COMPANY', :cp, 'Cross contract', 'SENT')"
                    " RETURNING public_id"
                ),
                {
                    "s": skeleton.sow,
                    "p": skeleton.project,
                    "c": tenant.company_id,
                    "cp": tenants["worker"].company_id,
                },
            )
        )
        .mappings()
        .one()
    )
    public_id = str(row["public_id"])
    contract_id = str(
        (
            await conn.execute(
                text("SELECT id FROM public.contracts WHERE public_id = :pid"),
                {"pid": public_id},
            )
        ).scalar_one()
    )
    await conn.execute(
        text(
            "INSERT INTO public.contract_roles (contract_id, project_role_id, quantity, rate)"
            " VALUES (CAST(:cid AS uuid), CAST(:prid AS uuid), 1, 75)"
        ),
        {"cid": contract_id, "prid": skeleton.project_role},
    )

    await act_as(tenants["worker"].user_id, tenants["worker"].company_id)
    accepted = await contracts.respond_to_contract(
        conn,
        company_id=tenants["worker"].company_id,
        public_id=public_id,
        accept=True,
        actor_user_id=tenants["worker"].user_id,
        request_id="pytest",
        ip_address=None,
        notes="Looks good",
    )
    assert accepted["status"] == "ACCEPTED"
    assert accepted["responded_by"] == tenants["worker"].user_public_id

    # A stranger still gets a 404, not a 403.
    import uuid

    from app.core.security import provision_user

    stranger_id = uuid.UUID(
        await provision_user(
            conn,
            str(uuid.uuid4()),
            email="stranger2@svc-fixture.test",
            first_name="Stranger",
            verified=True,
        )
    )
    await act_as(stranger_id, tenant.company_id)
    with pytest.raises(ResourceNotFoundError):
        await contracts.respond_to_contract(
            conn,
            company_id=None,
            public_id=public_id,
            accept=False,
            actor_user_id=stranger_id,
            request_id="pytest",
            ip_address=None,
            notes="nope",
        )


async def test_personal_sow_accept_creates_engagement_contract(conn, tenants) -> None:
    """§17: accepting an individual SOW yields its ACTIVE engagement contract."""
    from app.services import code, contracts

    user_id = tenants["admin"].user_id
    worker_public = tenants["worker"].user_public_id
    project = await _personal(conn, user_id)
    role = await code.create_personal_project_role(
        conn,
        user_id=user_id,
        project_public_id=project["public_id"],
        actor_user_id=user_id,
        request_id="pytest",
        ip_address=None,
        payload={"title": "Designer", "required_count": 1},
    )
    sow = await _personal_sow(
        conn,
        user_id,
        project["public_id"],
        counterparty=worker_public,
        roles=[{"project_role_id": role["public_id"], "quantity": 1, "rate": "80"}],
    )
    assert sow["status"] == "DRAFT"

    submitted = await code.submit_personal_sow(
        conn,
        user_id=user_id,
        public_id=sow["public_id"],
        actor_user_id=user_id,
        request_id="pytest",
        ip_address=None,
    )
    assert submitted["status"] == "PENDING_APPROVAL"

    accepted = await code.accept_sow(
        conn,
        company_id=None,
        user_id=user_id,
        public_id=sow["public_id"],
        actor_user_id=user_id,
        request_id="pytest",
        ip_address=None,
    )
    assert accepted["status"] == "ACTIVE"

    mine = await contracts.list_personal_contracts(conn, user_id=user_id, limit=10)
    assert len(mine) == 1
    engagement = mine[0]
    assert engagement["status"] == "ACTIVE"
    assert engagement["sow_id"] == sow["public_id"]
    assert engagement["company_id"] is None


async def test_personal_contract_explicit_lifecycle(conn, tenants, act_as) -> None:
    """§19: explicit per-assignment contracts under a personal SOW."""
    from app.services import code, contracts

    user_id = tenants["admin"].user_id
    project = await _personal(conn, user_id)
    role = await code.create_personal_project_role(
        conn,
        user_id=user_id,
        project_public_id=project["public_id"],
        actor_user_id=user_id,
        request_id="pytest",
        ip_address=None,
        payload={"title": "Writer", "required_count": 2},
    )
    sow = await _personal_sow(
        conn,
        user_id,
        project["public_id"],
        counterparty=tenants["worker"].user_public_id,
        roles=[{"project_role_id": role["public_id"], "quantity": 2}],
    )

    contract = await contracts.create_personal_contract(
        conn,
        user_id=user_id,
        sow_public_id=sow["public_id"],
        actor_user_id=user_id,
        request_id="pytest",
        ip_address=None,
        payload={
            "project_id": project["public_id"],
            "title": "Writing engagement",
            "roles": [{"project_role_id": role["public_id"], "quantity": 1, "rate": "60"}],
        },
    )
    assert contract["status"] == "DRAFT"
    assert contract["company_id"] is None
    assert [r["project_role_id"] for r in contract["roles"]] == [role["public_id"]]

    # A role from another project is refused.
    other_project = await _personal(conn, user_id)
    other_role = await code.create_personal_project_role(
        conn,
        user_id=user_id,
        project_public_id=other_project["public_id"],
        actor_user_id=user_id,
        request_id="pytest",
        ip_address=None,
        payload={"title": "Foreign", "required_count": 1},
    )
    with pytest.raises(BusinessRuleViolationError) as caught:
        await contracts.create_personal_contract(
            conn,
            user_id=user_id,
            sow_public_id=sow["public_id"],
            actor_user_id=user_id,
            request_id="pytest",
            ip_address=None,
            payload={
                "project_id": project["public_id"],
                "title": "Bad engagement",
                "roles": [{"project_role_id": other_role["public_id"], "quantity": 1}],
            },
        )
    assert caught.value.details["reason"] == "PROJECT_ROLE_NOT_ON_PROJECT"

    # Send, then the counterparty user accepts and the owner terminates.
    sent = await contracts.transition_personal_contract(
        conn,
        user_id=user_id,
        public_id=contract["public_id"],
        target="SENT",
        actor_user_id=user_id,
        request_id="pytest",
        ip_address=None,
    )
    assert sent["status"] == "SENT"

    await act_as(tenants["worker"].user_id, tenants["worker"].company_id)
    accepted = await contracts.respond_to_contract(
        conn,
        company_id=None,
        public_id=contract["public_id"],
        accept=True,
        actor_user_id=tenants["worker"].user_id,
        request_id="pytest",
        ip_address=None,
        notes=None,
    )
    assert accepted["status"] == "ACCEPTED"

    await act_as(user_id, tenants["admin"].company_id)
    active = await contracts.transition_personal_contract(
        conn,
        user_id=user_id,
        public_id=contract["public_id"],
        target="ACTIVE",
        actor_user_id=user_id,
        request_id="pytest",
        ip_address=None,
    )
    assert active["status"] == "ACTIVE"
    terminated = await contracts.transition_personal_contract(
        conn,
        user_id=user_id,
        public_id=contract["public_id"],
        target="TERMINATED",
        actor_user_id=user_id,
        request_id="pytest",
        ip_address=None,
        reason="Scope complete",
    )
    assert terminated["status"] == "TERMINATED"
