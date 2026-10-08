"""Profile enrichment: education, experience, skills and visa status.

Career history is owned by its user: entries can only be listed, added and
removed as oneself, and the API validates employment types, date ranges and
skill proficiency before the database CHECKs ever see them.
"""

from __future__ import annotations

import pytest

from app.core.errors import ResourceNotFoundError, ValidationError
from app.services import identity as identity_service

pytestmark = pytest.mark.integration


async def test_education_round_trip(conn, tenants) -> None:
    user_id = tenants["admin"].user_id
    created = await identity_service.add_education(
        conn,
        user_id=user_id,
        payload={
            "institution": "IIT Bombay",
            "degree": "B.Tech",
            "field_of_study": "Computer Science",
            "start_date": "2015-08-01",
            "end_date": "2019-05-01",
        },
        request_id="pytest",
    )
    assert created["id"]

    rows = await identity_service.list_education(conn, user_id=user_id)
    assert [r["institution"] for r in rows] == ["IIT Bombay"]

    await identity_service.remove_education(
        conn, user_id=user_id, education_id=created["id"], request_id="pytest"
    )
    assert await identity_service.list_education(conn, user_id=user_id) == []

    with pytest.raises(ResourceNotFoundError):
        await identity_service.remove_education(
            conn, user_id=user_id, education_id=created["id"], request_id="pytest"
        )


async def test_education_validates_its_inputs(conn, tenants) -> None:
    user_id = tenants["admin"].user_id
    with pytest.raises(ValidationError):
        await identity_service.add_education(
            conn, user_id=user_id, payload={"degree": "B.Tech"}, request_id="pytest"
        )
    with pytest.raises(ValidationError):
        await identity_service.add_education(
            conn,
            user_id=user_id,
            payload={
                "institution": "IIT Bombay",
                "start_date": "2019-05-01",
                "end_date": "2015-08-01",
            },
            request_id="pytest",
        )


async def test_experience_round_trip_with_employment_check(conn, tenants) -> None:
    user_id = tenants["admin"].user_id
    created = await identity_service.add_experience(
        conn,
        user_id=user_id,
        payload={
            "company_name": "Acme Robotics",
            "title": "Site Engineer",
            "employment_type": "FULL_TIME",
            "start_date": "2021-06-01",
            "is_current": True,
        },
        request_id="pytest",
    )
    rows = await identity_service.list_experience(conn, user_id=user_id)
    assert [r["title"] for r in rows] == ["Site Engineer"]

    with pytest.raises(ValidationError):
        await identity_service.add_experience(
            conn,
            user_id=user_id,
            payload={"title": "Ghost", "employment_type": "VOLUNTEER"},
            request_id="pytest",
        )

    await identity_service.remove_experience(
        conn, user_id=user_id, experience_id=created["id"], request_id="pytest"
    )
    assert await identity_service.list_experience(conn, user_id=user_id) == []


async def test_skills_attach_update_detach(conn, tenants) -> None:
    user_id = tenants["admin"].user_id
    first = await identity_service.attach_skill(
        conn,
        user_id=user_id,
        payload={"skill_name": "Welding", "proficiency": 4},
        request_id="pytest",
    )
    # Re-attaching updates the existing row rather than duplicating it.
    second = await identity_service.attach_skill(
        conn,
        user_id=user_id,
        payload={"skill_name": "Welding", "proficiency": 5},
        request_id="pytest",
    )
    assert first["skill_id"] == second["skill_id"]

    rows = await identity_service.list_skills(conn, user_id=user_id)
    assert [(r["name"], r["proficiency"]) for r in rows] == [("Welding", 5)]

    with pytest.raises(ValidationError):
        await identity_service.attach_skill(
            conn,
            user_id=user_id,
            payload={"skill_name": "Welding", "proficiency": 9},
            request_id="pytest",
        )

    await identity_service.detach_skill(
        conn, user_id=user_id, skill_id=first["skill_id"], request_id="pytest"
    )
    assert await identity_service.list_skills(conn, user_id=user_id) == []


async def test_career_entries_are_owned_by_their_user(conn, tenants) -> None:
    worker_id = tenants["worker"].user_id
    created = await identity_service.add_education(
        conn,
        user_id=worker_id,
        payload={"institution": "Private College"},
        request_id="pytest",
    )
    # Another user cannot see or delete it through the owner-scoped functions.
    assert await identity_service.list_education(conn, user_id=tenants["admin"].user_id) == []
    with pytest.raises(ResourceNotFoundError):
        await identity_service.remove_education(
            conn,
            user_id=tenants["admin"].user_id,
            education_id=created["id"],
            request_id="pytest",
        )


async def test_visa_status_is_stored_on_the_profile(conn, tenants) -> None:
    user_id = tenants["admin"].user_id
    await identity_service.update_me(
        conn,
        user_id=user_id,
        changes={"visa_status": "WORK_VISA", "years_experience": 6},
        request_id="pytest",
        ip_address=None,
    )
    me = await identity_service.get_me(conn, user_id)
    assert me["visa_status"] == "WORK_VISA"
    assert me["years_experience"] == 6
