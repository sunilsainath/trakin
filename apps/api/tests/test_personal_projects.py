"""CODE Phase 1: personal (INDIVIDUAL) projects + project status transitions.

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
