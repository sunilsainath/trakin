"""Timesheet file import: reviewed rows bulk-create atomically.

One bad row refuses the whole file rather than importing half of it, and only
the sheet owner may import into a draft (or rejected) sheet.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.core.errors import BusinessRuleViolationError
from app.services import timesheet_import as import_service

pytestmark = pytest.mark.integration


async def _draft_sheet(conn, skeleton, tenants) -> str:
    worker = tenants["worker"]
    return (
        await conn.execute(
            text(
                """
                INSERT INTO public.timesheets
                  (user_id, company_id, assignment_id, contract_id, contract_role_id,
                   project_id, period_start, period_end, billing_frequency, status,
                   currency)
                VALUES
                  (CAST(:user AS uuid), CAST(:company AS uuid), CAST(:asg AS uuid),
                   CAST(:contract AS uuid), CAST(:cr AS uuid), CAST(:project AS uuid),
                   '2026-09-01', '2026-09-30', 'MONTHLY', 'DRAFT', 'USD')
                RETURNING public_id
                """
            ),
            {
                "user": worker.user_id,
                "company": tenants["admin"].company_id,
                "asg": skeleton.assignment,
                "contract": skeleton.contract,
                "cr": skeleton.contract_role,
                "project": skeleton.project,
            },
        )
    ).scalar_one()


def _call(conn, tenants, public_id, entries):
    worker = tenants["worker"]
    return import_service.bulk_add_entries(
        conn,
        company_id=tenants["admin"].company_id,
        public_id=public_id,
        actor_user_id=worker.user_id,
        request_id="pytest",
        ip_address=None,
        entries=entries,
    )


async def test_bulk_import_records_reviewed_rows(conn, skeleton, tenants) -> None:
    public_id = await _draft_sheet(conn, skeleton, tenants)
    sheet = await _call(
        conn,
        tenants,
        public_id,
        [
            {"entry_date": "2026-09-03", "hours": 8, "work_description": "Backend work"},
            {"entry_date": "2026-09-04", "hours": 6.5, "work_description": "Reviews"},
        ],
    )
    assert sheet["entry_count"] == 2
    assert float(sheet["total_hours"]) == 14.5


async def test_bulk_import_refuses_the_whole_file_on_one_bad_row(conn, skeleton, tenants) -> None:
    public_id = await _draft_sheet(conn, skeleton, tenants)
    with pytest.raises(BusinessRuleViolationError) as exc_info:
        await _call(
            conn,
            tenants,
            public_id,
            [
                {"entry_date": "2026-09-03", "hours": 8, "work_description": "Good row"},
                {"entry_date": "2026-10-01", "hours": 8, "work_description": "Outside"},
            ],
        )
    assert exc_info.value.details["reason"] == "IMPORT_ROWS_INVALID"

    count = (
        await conn.execute(
            text(
                "SELECT count(*) FROM public.timesheet_entries WHERE timesheet_id = "
                "(SELECT id FROM public.timesheets WHERE public_id = :pid)"
            ),
            {"pid": public_id},
        )
    ).scalar_one()
    assert count == 0


async def test_bulk_import_rejects_a_strangers_sheet(conn, skeleton, tenants) -> None:
    admin = tenants["admin"]
    public_id = await _draft_sheet(conn, skeleton, tenants)
    with pytest.raises(BusinessRuleViolationError) as exc_info:
        await import_service.bulk_add_entries(
            conn,
            company_id=admin.company_id,
            public_id=public_id,
            actor_user_id=admin.user_id,
            request_id="pytest",
            ip_address=None,
            entries=[{"entry_date": "2026-09-03", "hours": 8, "work_description": "Hi"}],
        )
    assert exc_info.value.details["reason"] == "TIMESHEET_NOT_OWNED"
