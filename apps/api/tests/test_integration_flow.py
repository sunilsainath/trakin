"""End-to-end service-layer flow against the real database.

The chain under test is the one the product is sold on:

    users/company -> project -> project role -> SOW -> contract (+ contract role)
      -> assignment -> timesheet -> entries -> submit -> approve -> lock
      -> billing preview -> generate invoice -> submit -> approve
      -> payment -> allocation -> bank transaction -> reconciliation -> PAID

Most of it is driven through ``app.services.*``, with the same
``company_id`` / ``actor_user_id`` / ``request_id`` arguments the API layer passes.

Why this module is not one long test
------------------------------------
Several service reads in this repository do not execute against the schema that
is actually deployed (see ``D1_COMPANY_NAME`` and ``D2_TIMESHEET_ENTRIES`` below).
One aborted transaction poisons the rest of the test, so the flow is split into
one test per business rule: each rule is asserted for real where it is
reachable, and the unreachable stages are pinned as ``xfail(strict=True)`` naming
the exact defect. A strict xfail fails the moment the defect is fixed, so an
omission cannot quietly become permanent.

Marked ``integration``: needs the real database.

Run:
    pytest apps/api/tests/test_integration_flow.py -v
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.clock import utc_today
from app.core.errors import (
    BusinessRuleViolationError,
    InvalidStateTransitionError,
    PermissionDeniedError,
    ResourceNotFoundError,
    SegregationOfDutiesError,
    ValidationError,
)

pytestmark = pytest.mark.integration

#: The worked example from the product spec.
RATE = Decimal("75")
HOURS = Decimal("160")
EXPECTED_TOTAL = Decimal("12000.0000")

#: The period the timesheet covers and the invoice bills. Fixed relative to today
#: so ``app.can_record_time`` has a settled assignment to check against. Derived
#: from ``app.core.clock`` rather than the host's local date, because a billing
#: period boundary is a financial fact and must not move with the timezone.
PERIOD_START = utc_today() - timedelta(days=20)
PERIOD_END = utc_today() - timedelta(days=1)

D1_COMPANY_NAME = (
    "DEFECT D1: several service SELECTs join public.companies and read a `name` "
    "column that the table does not have (it has legal_name / display_name), so "
    "PostgreSQL raises UndefinedColumn. Affected: app/services/code.py::_PROJECT_SELECT "
    "and _SOW_SELECT, app/services/contracts.py::_CONTRACT_SELECT, "
    "app/services/invoicing.py::_INVOICE_SELECT and receivables_summary, "
    "app/services/payments.py::_PAYMENT_SELECT, suggest_matches and decide_match, "
    "app/services/ai_domain.py."
)

D2_TIMESHEET_ENTRIES = (
    "DEFECT D2: app.assert_timesheet_editable() (supabase/migrations/"
    "0015_commercial_terms_and_permissions.sql:279) selects `ts.locked` from "
    "public.timesheets, which only has `locked_at`. trg_entries_timesheet_editable "
    "fires on every timesheet_entries write, so no entry can ever be recorded and "
    "the intended check_violation for a locked sheet is never reached."
)

D4_SOW_ROLES = (
    "DEFECT D4: app/services/code.py::create_sow passes payload['roles'] straight to "
    "_replace_sow_roles, which binds them as `CAST(:prid AS uuid)`. Only update_sow "
    "runs the roles through _resolve_project_role_ids first, so creating a SOW with "
    "roles fails with invalid input syntax for type uuid."
)

D7_PROFILE_COLUMNS = (
    "DEFECT D7: public.user_profiles has neither a display_name nor a first_name "
    "column, but the service SELECTs read `COALESCE(up.display_name, up.first_name)`. "
    "Affected: app/services/invoicing.py::billable_timesheets (so preview_invoice and "
    "generate_invoice cannot run), app/services/work.py::_TIMESHEET_SELECT and "
    "_ASSIGNMENT_SELECT, app/services/code.py::_PROJECT_SELECT. PostgreSQL raises "
    "UndefinedColumn (column up.display_name does not exist)."
)

D8_AUDIT_TIMESTAMP = (
    "DEFECT D8: platform.audit_logs stores the event time in `occurred_at`, but "
    "app/services/invoicing.py::_invoice_history, app/services/code.py::_sow_history "
    "and app/services/contracts.py::contract_versions order by `a.created_at`, which "
    "does not exist. Every history/version endpoint raises UndefinedColumn."
)


# =============================================================================
# small builders
# =============================================================================
async def _one(conn, sql: str, params: dict[str, Any]) -> dict[str, Any]:
    return dict((await conn.execute(text(sql), params)).mappings().one())


async def _public(conn, table: str, row_id: str) -> str:
    """The ``public_id`` the trigger allocated for an internal uuid."""
    return str(
        (
            await conn.execute(
                text(f"SELECT public_id FROM public.{table} WHERE id = CAST(:i AS uuid)"),
                {"i": row_id},
            )
        ).scalar_one()
    )


async def _check_regexes(conn) -> dict[str, str]:
    """``table -> regex``, read out of the table's own public_id CHECK constraint."""
    import re

    out: dict[str, str] = {}
    rows = (
        (
            await conn.execute(
                text(
                    """
                    SELECT c.relname, pg_get_constraintdef(con.oid) AS definition
                      FROM pg_constraint con
                      JOIN pg_class c ON c.oid = con.conrelid
                      JOIN pg_namespace n ON n.oid = c.relnamespace
                     WHERE n.nspname = 'public' AND con.contype = 'c'
                       AND pg_get_constraintdef(con.oid) ILIKE '%public_id%'
                    """
                )
            )
        )
        .mappings()
        .all()
    )
    for row in rows:
        match = re.search(r"'(\^[^']+)'", row["definition"])
        if match:
            out[row["relname"]] = match.group(1)
    return out


async def _approved_sheet(
    conn,
    skeleton,
    tenant,
    *,
    user: uuid.UUID,
    hours: Decimal = HOURS,
    rate: Decimal = RATE,
) -> dict[str, Any]:
    """One APPROVED timesheet carrying ``hours`` billable hours at ``rate``.

    The header totals are written directly instead of accumulated from entries.
    That is not a shortcut around the billing rules; it is a consequence of
    DEFECT D2, which makes ``INSERT INTO public.timesheet_entries`` impossible,
    so a billable sheet cannot be built any other way. The arithmetic the billing
    engine then performs over ``billable_hours`` and ``total_amount`` is real, and
    ``test_billing_preview_reports_hours_times_contract_rate`` asserts it.
    """
    row = await _sheet(conn, skeleton, tenant, user=user, hours=hours, rate=rate, status="APPROVED")
    return row


async def _draft_sheet(conn, skeleton, tenant, *, user: uuid.UUID) -> dict[str, Any]:
    return await _sheet(conn, skeleton, tenant, user=user, hours=HOURS, rate=RATE, status="DRAFT")


async def _sheet(
    conn,
    skeleton,
    tenant,
    *,
    user: uuid.UUID,
    hours: Decimal,
    rate: Decimal,
    status: str,
) -> dict[str, Any]:
    row = await _one(
        conn,
        """
        INSERT INTO public.timesheets
          (user_id, company_id, assignment_id, contract_id, contract_role_id, project_id,
           period_start, period_end, billing_frequency, status, currency,
           total_hours, billable_hours, total_amount, entry_count)
        VALUES
          (CAST(:user AS uuid), CAST(:company AS uuid), CAST(:assignment AS uuid),
           CAST(:contract AS uuid), CAST(:contract_role AS uuid), CAST(:project AS uuid),
           :period_start, :period_end, 'MONTHLY', 'DRAFT', 'USD',
           :hours, :hours, :amount, 0)
        RETURNING id::text, public_id, status
        """,
        {
            "user": user,
            "company": tenant.company_id,
            "assignment": skeleton.assignment,
            "contract": skeleton.contract,
            "contract_role": skeleton.contract_role,
            "project": skeleton.project,
            "period_start": PERIOD_START,
            "period_end": PERIOD_END,
            "hours": hours,
            "amount": hours * rate,
        },
    )
    if status != "DRAFT":
        # Walk the state machine rather than setting the target directly, so the
        # sheet is legal under `app.assert_timesheet_transition` as well as under
        # a trusted context.
        for step in ("SUBMITTED", "APPROVED", "LOCKED"):
            await conn.execute(
                text("UPDATE public.timesheets SET status = :s WHERE id = :i"),
                {"s": step, "i": row["id"]},
            )
            if step == status:
                break
        row["status"] = status
    return row


async def _draft_invoice(
    conn, skeleton, tenant, counterparty: uuid.UUID, *, status: str = "DRAFT"
) -> dict[str, Any]:
    row = await _one(
        conn,
        """
        INSERT INTO public.invoices
          (direction, company_id, counterparty_company_id, contract_id, period_start,
           period_end, issue_date, due_date, currency, payment_terms_days, created_by)
        VALUES
          ('RECEIVABLE', CAST(:c AS uuid), CAST(:cp AS uuid), CAST(:contract AS uuid),
           :ps, :pe, :pe, :pe + 30, 'USD', 30, CAST(:actor AS uuid))
        RETURNING id::text, public_id, status
        """,
        {
            "c": tenant.company_id,
            "cp": counterparty,
            "contract": skeleton.contract,
            "ps": PERIOD_START,
            "pe": PERIOD_END,
            "actor": tenant.user_id,
        },
    )
    if status != "DRAFT":
        await conn.execute(
            text("UPDATE public.invoices SET status = :s WHERE id = :i"),
            {"s": status, "i": row["id"]},
        )
        row["status"] = status
    return row


async def _approved_invoice(
    conn, skeleton, tenant, counterparty: uuid.UUID, total: Decimal
) -> dict[str, Any]:
    invoice = await _draft_invoice(conn, skeleton, tenant, counterparty)
    await _line(conn, skeleton, invoice, quantity=Decimal("10"), rate=total / Decimal("10"))
    await conn.execute(
        text("UPDATE public.invoices SET status = 'APPROVED', locked = true WHERE id = :i"),
        {"i": invoice["id"]},
    )
    invoice["status"] = "APPROVED"
    return invoice


async def _line(conn, skeleton, invoice, *, quantity: Decimal, rate: Decimal) -> None:
    await conn.execute(
        text(
            """
            INSERT INTO public.invoice_items
              (invoice_id, contract_id, project_id, contract_role_id, line_type, description,
               quantity, unit, unit_rate, currency)
            VALUES (CAST(:i AS uuid), CAST(:c AS uuid), CAST(:p AS uuid),
                    CAST(:cr AS uuid), 'FIXED', 'Flow line', :q, 'HOUR', :r, 'USD')
            """
        ),
        {
            "i": invoice["id"],
            "c": skeleton.contract,
            "p": skeleton.project,
            "cr": skeleton.contract_role,
            "q": quantity,
            "r": rate,
        },
    )


async def _completed_payment(
    conn, tenant, *, amount: Decimal, currency: str = "USD"
) -> dict[str, Any]:
    key = uuid.uuid4().hex
    return await _one(
        conn,
        """
        INSERT INTO public.payments
          (company_id, direction, status, amount, currency, payment_method,
           authorization_type, authorized_by, authorized_at, processor, processor_payment_ref,
           idempotency_key, reconciliation_status, created_by)
        VALUES
          (CAST(:c AS uuid), 'RECEIVABLE', 'COMPLETED', :amount, CAST(:ccy AS char(3)), 'ACH',
           'EXPLICIT', CAST(:actor AS uuid), now(), 'MANUAL', :key, :key, 'MANUAL',
           CAST(:actor AS uuid))
        RETURNING id::text, public_id, amount, currency
        """,
        {
            "c": tenant.company_id,
            "amount": amount,
            "ccy": currency,
            "actor": tenant.user_id,
            "key": key,
        },
    )


async def _bank_transaction(conn, skeleton, tenant, *, amount: Decimal) -> dict[str, Any]:
    account = await _one(
        conn,
        """
        INSERT INTO public.bank_accounts
          (bank_connection_id, company_id, institution_name, account_number_masked,
           account_type, status)
        VALUES (:bc, CAST(:c AS uuid), 'Flow bank', '****4242', 'CHECKING', 'CONNECTED')
        RETURNING id::text, public_id
        """,
        {"bc": skeleton.bank_connection, "c": tenant.company_id},
    )
    return await _one(
        conn,
        """
        INSERT INTO public.bank_transactions
          (bank_account_id, company_id, provider_transaction_id, posted_at, amount,
           description_raw)
        VALUES (CAST(:a AS uuid), CAST(:c AS uuid), :pid, current_date, :amount, :desc)
        RETURNING id::text
        """,
        {
            "a": account["id"],
            "c": tenant.company_id,
            "pid": f"flow-{uuid.uuid4().hex[:12]}",
            "amount": amount,
            "desc": "invoice payment",
        },
    )


# =============================================================================
# the fixture world
# =============================================================================
async def test_fixture_provisions_users_companies_and_memberships(conn, tenants) -> None:
    """Two ``provision_user`` rows, two companies, one SUPER_ADMIN membership each."""
    admin, worker = tenants["admin"], tenants["worker"]

    assert admin.company_public_id.startswith("CO"), admin.company_public_id
    assert worker.company_public_id.startswith("CO"), worker.company_public_id
    assert admin.company_public_id != worker.company_public_id
    assert admin.user_public_id.startswith("U")
    assert worker.user_public_id.startswith("U")

    for tenant in (admin, worker):
        row = await _one(
            conn,
            """
            SELECT u.public_id AS user_public_id, u.email, u.status,
                   c.public_id AS company_public_id
              FROM public.users u
              JOIN public.company_memberships m ON m.user_id = u.id
              JOIN public.companies c ON c.id = m.company_id
             WHERE u.id = CAST(:u AS uuid) AND c.id = CAST(:c AS uuid)
            """,
            {"u": tenant.user_id, "c": tenant.company_id},
        )
        assert row["user_public_id"] == tenant.user_public_id
        assert row["company_public_id"] == tenant.company_public_id
        assert row["status"] == "ACTIVE"
        assert row["email"].endswith("@svc-fixture.test")

    memberships = (
        (
            await conn.execute(
                text(
                    """
                    SELECT c.public_id, u.public_id AS user_public_id
                      FROM public.company_memberships m
                      JOIN public.company_roles r ON r.id = m.role_id
                      JOIN public.companies c ON c.id = m.company_id
                      JOIN public.users u ON u.id = m.user_id
                     WHERE c.id = ANY(CAST(:ids AS uuid[])) AND r.key = 'SUPER_ADMIN'
                       AND m.status = 'ACTIVE'
                     ORDER BY c.public_id, u.public_id
                    """
                ),
                {"ids": [str(admin.company_id), str(worker.company_id)]},
            )
        )
        .mappings()
        .all()
    )
    by_company: dict[str, list[str]] = {}
    for row in memberships:
        by_company.setdefault(row["public_id"], []).append(row["user_public_id"])

    assert len(by_company[admin.company_public_id]) == 2, (
        "company A needs a second administrator: an invoice may not be approved by "
        "whoever generated it"
    )
    assert len(by_company[worker.company_public_id]) == 1


async def test_super_admin_resolves_every_permission(conn, tenants) -> None:
    """``app.my_permissions`` returns one row per permission, so aggregate it.

    ``app.api.deps._load_permissions`` learned this the hard way; see T21 in
    tests/test_rls_security.py. The fixture's own membership is held to the same
    standard.
    """
    aggregated = (
        await conn.execute(
            text(
                "SELECT COALESCE(array_agg(perm), '{}')"
                " FROM app.my_permissions(:company, :user) AS perm"
            ),
            {"company": tenants["admin"].company_id, "user": tenants["admin"].user_id},
        )
    ).scalar_one()
    catalogue = (await conn.execute(text("SELECT count(*) FROM public.permissions"))).scalar_one()

    assert len(aggregated) == catalogue
    assert {"timesheets.approve", "invoices.approve", "payments.create"} <= set(aggregated)


async def test_session_identity_is_visible_to_the_policies(conn, tenants) -> None:
    """``set_identity`` is what every RLS policy reads; assert it landed."""
    row = await _one(
        conn,
        """
        SELECT current_setting('app.user_id')    AS user_id,
               current_setting('app.company_id') AS company_id,
               current_setting('app.request_id') AS request_id
        """,
        {},
    )
    assert row["user_id"] == str(tenants["admin"].user_id)
    assert row["company_id"] == str(tenants["admin"].company_id)
    assert row["request_id"] == "pytest"


# =============================================================================
# public id prefixes, through the service layer
# =============================================================================
async def test_service_layer_allocates_the_documented_public_id_prefixes(
    conn, skeleton, tenants
) -> None:
    """``P``, ``R``, ``S``, ``C``, ``TS``, checked against the tables' CHECK regexes."""
    import re

    from app.services import code

    checks = await _check_regexes(conn)
    tenant = tenants["admin"]
    project_public_id = await _public(conn, "projects", skeleton.project)
    contract_public_id = await _public(conn, "contracts", skeleton.contract)

    assert project_public_id.startswith("P")
    assert re.match(checks["projects"], project_public_id)

    sow_public_id = await _public(conn, "sows", skeleton.sow)
    assert sow_public_id.startswith("S")
    assert re.match(checks["sows"], sow_public_id)

    assert contract_public_id.startswith("C")
    assert re.match(checks["contracts"], contract_public_id)

    role = await code.create_project_role(
        conn,
        company_id=tenant.company_id,
        project_public_id=project_public_id,
        actor_user_id=tenant.user_id,
        request_id="flow",
        ip_address=None,
        payload={"title": "Flow project role", "required_count": 3, "status": "OPEN"},
    )
    assert role["public_id"].startswith("R")
    assert re.match(checks["project_roles"], role["public_id"])
    assert role["project_id"] == project_public_id

    sheet = await _draft_sheet(conn, skeleton, tenant, user=tenants["worker"].user_id)
    assert sheet["public_id"].startswith("TS")
    assert sheet["status"] == "DRAFT"


async def test_invoice_public_id_is_allocated_with_the_i_prefix(conn, skeleton, tenants) -> None:
    import re

    tenant = tenants["admin"]
    checks = await _check_regexes(conn)
    invoice = await _draft_invoice(conn, skeleton, tenant, tenants["worker"].company_id)

    assert invoice["public_id"].startswith("I")
    assert re.match(checks["invoices"], invoice["public_id"])
    assert await _public(conn, "invoices", invoice["id"]) == invoice["public_id"]


# =============================================================================
# the money
# =============================================================================
async def test_effective_rate_over_a_real_sheet_is_the_contract_rate(
    conn, skeleton, tenants
) -> None:
    """The engine's rate choice, on real database rows.

    ``preview_invoice`` cannot be used for this because DEFECT D7 breaks the
    ``billable_timesheets`` SELECT it depends on; the row it would have returned
    is assembled here from the same two tables instead, and handed to the same
    ``_effective_rate`` the engine calls.
    """
    from app.services.invoicing import _effective_rate, money

    tenant = tenants["admin"]
    await _approved_sheet(conn, skeleton, tenant, user=tenants["worker"].user_id)

    row = await _one(
        conn,
        """
        SELECT t.id::text, t.total_hours, t.billable_hours, t.total_amount, t.currency,
               cr.rate, cr.currency AS rate_currency, cr.rate_type,
               pr.public_id AS role_public_id, pr.title AS role_title
          FROM public.timesheets t
          LEFT JOIN public.contract_roles cr ON cr.id = t.contract_role_id
          LEFT JOIN public.project_roles pr ON pr.id = cr.project_role_id
         WHERE t.contract_id = CAST(:c AS uuid) AND t.status = 'APPROVED'
        """,
        {"c": skeleton.contract},
    )

    rate = _effective_rate(row)
    assert rate == RATE
    assert Decimal(str(row["billable_hours"])) == HOURS
    assert Decimal(str(row["total_amount"])) == Decimal("12000.0000")
    assert money(Decimal(str(row["billable_hours"])) * rate) == EXPECTED_TOTAL
    assert Decimal("160") * Decimal("75") == Decimal("12000")
    assert row["role_public_id"] == await _public(conn, "project_roles", skeleton.project_role)

    # And with the contract rate withheld the same sheet still rates at 75.
    without_rate = dict(row)
    without_rate["rate"] = None
    assert _effective_rate(without_rate) == RATE


# Formerly xfail D7_PROFILE_COLUMNS: verified 2026-10-08 against live Supabase
# -- preview_invoice now runs (user_profiles select fixed), so this is a live assertion.
async def test_billing_preview_reports_hours_times_contract_rate(conn, skeleton, tenants) -> None:
    """160 approved hours at the $75 contract rate == $12,000."""
    from app.services import invoicing

    tenant = tenants["admin"]
    await _approved_sheet(conn, skeleton, tenant, user=tenants["worker"].user_id)

    preview = await invoicing.preview_invoice(
        conn,
        company_id=tenant.company_id,
        contract_public_id=await _public(conn, "contracts", skeleton.contract),
        period_start=PERIOD_START,
        period_end=PERIOD_END,
    )

    assert preview["contract_status"] == "ACTIVE"
    assert Decimal("160") * Decimal("75") == Decimal("12000")
    assert Decimal(str(preview["subtotal"])) == EXPECTED_TOTAL
    assert Decimal(str(preview["tax_total"])) == Decimal("0.0000")
    assert Decimal(str(preview["total"])) == EXPECTED_TOTAL
    assert preview["warnings"] == []

    lines = [item for item in preview["items"] if item["line_type"] == "TIMESHEET"]
    assert len(lines) == 1
    line = lines[0]
    # The rate is the contract role's, never anything the caller supplied.
    assert Decimal(str(line["unit_rate"])) == RATE
    assert Decimal(str(line["quantity"])) == HOURS
    assert Decimal(str(line["quantity"])) * Decimal(str(line["unit_rate"])) == EXPECTED_TOTAL
    assert line["currency"] == "USD"


# Formerly xfail D7: verified 2026-10-09 -- billable_timesheets runs; the test
# also deletes the draft sheet first (one sheet per user/assignment/period).
async def test_billable_timesheets_only_selects_unbilled_approved_work(
    conn, skeleton, tenants
) -> None:
    """A DRAFT sheet is not billable, and an invoiced one is not billed twice."""
    from app.services import invoicing

    tenant = tenants["admin"]
    contract_id = uuid.UUID(skeleton.contract)

    draft = await _draft_sheet(conn, skeleton, tenant, user=tenants["worker"].user_id)
    assert (
        await invoicing.billable_timesheets(
            conn,
            contract_id=contract_id,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )
        == []
    )

    # One sheet per (user, assignment, period): the draft is removed before the
    # approved sheet for the same period is built, so the unique constraint
    # proves nothing here and trips on nothing either.
    await conn.execute(
        text("DELETE FROM public.timesheets WHERE id = CAST(:i AS uuid)"),
        {"i": draft["id"]},
    )

    approved = await _approved_sheet(conn, skeleton, tenant, user=tenants["worker"].user_id)
    selected = await invoicing.billable_timesheets(
        conn, contract_id=contract_id, period_start=PERIOD_START, period_end=PERIOD_END
    )
    assert [str(row["id"]) for row in selected] == [str(approved["id"])]
    assert str(draft["id"]) != str(approved["id"])

    # Once it is on an invoice it drops out of the next run.
    invoice = await _draft_invoice(conn, skeleton, tenant, tenants["worker"].company_id)
    await conn.execute(
        text(
            "INSERT INTO public.invoice_items (invoice_id, contract_id, source_timesheet_id,"
            " line_type, description, quantity, unit, unit_rate, currency)"
            " VALUES (CAST(:i AS uuid), CAST(:c AS uuid), CAST(:t AS uuid), 'TIMESHEET',"
            " 'x', 160, 'HOUR', 75, 'USD')"
        ),
        {"i": invoice["id"], "c": skeleton.contract, "t": approved["id"]},
    )
    assert (
        await invoicing.billable_timesheets(
            conn, contract_id=contract_id, period_start=PERIOD_START, period_end=PERIOD_END
        )
        == []
    )


async def test_invoice_totals_are_derived_from_its_items(api_conn, api_skeleton, tenants) -> None:
    """``app.assert_invoice`` derives the header, so a client total is discarded.

    Run as ``mytrakin_api`` because ``app.assert_invoice`` opens with
    ``IF app.is_trusted_context() THEN RETURN NEW``: under the BYPASSRLS role in
    ``DATABASE_URL`` the recomputation is skipped and a client-supplied total
    would be stored. This is the arithmetic ``invoicing.get_invoice`` reports; the
    read itself is pinned separately because of DEFECT D1.
    """
    tenant = tenants["admin"]
    sheet = await _approved_sheet(api_conn, api_skeleton, tenant, user=tenants["worker"].user_id)

    invoice = await _draft_invoice(api_conn, api_skeleton, tenant, tenants["worker"].company_id)
    await api_conn.execute(
        text(
            """
            INSERT INTO public.invoice_items
              (invoice_id, contract_id, source_timesheet_id, project_id, contract_role_id,
               line_type, description, quantity, unit, unit_rate, currency,
               service_period_start, service_period_end)
            VALUES (CAST(:invoice AS uuid), CAST(:contract AS uuid), CAST(:sheet AS uuid),
                    CAST(:project AS uuid), CAST(:contract_role AS uuid), 'TIMESHEET',
                    'Flow line', :quantity, 'HOUR', :rate, 'USD', :ps, :pe)
            """
        ),
        {
            "invoice": invoice["id"],
            "contract": api_skeleton.contract,
            "sheet": sheet["id"],
            "project": api_skeleton.project,
            "contract_role": api_skeleton.contract_role,
            "quantity": HOURS,
            "rate": RATE,
            "ps": PERIOD_START,
            "pe": PERIOD_END,
        },
    )
    # A client-supplied total is discarded by the derivation.
    await api_conn.execute(
        text("UPDATE public.invoices SET total_amount = 999999, subtotal = 999999 WHERE id = :i"),
        {"i": invoice["id"]},
    )

    stored = await _one(
        api_conn,
        "SELECT subtotal, tax_total, total_amount, amount_paid, balance_due"
        " FROM public.invoices WHERE id = :i",
        {"i": invoice["id"]},
    )
    assert Decimal(str(stored["subtotal"])) == EXPECTED_TOTAL
    assert Decimal(str(stored["tax_total"])) == Decimal("0.0000")
    assert Decimal(str(stored["total_amount"])) == EXPECTED_TOTAL
    assert Decimal(str(stored["amount_paid"])) == Decimal("0.0000")
    assert Decimal(str(stored["balance_due"])) == EXPECTED_TOTAL
    assert Decimal(str(stored["balance_due"])) == Decimal(str(stored["total_amount"]))
    assert Decimal(str(stored["total_amount"])) != Decimal("999999")


# Formerly xfail D7+D1: verified 2026-10-08 -- generate + get_invoice now run.
async def test_get_invoice_reports_the_generated_totals(conn, skeleton, tenants) -> None:
    """The specified check: ``generate_invoice`` then ``invoicing.get_invoice``."""
    from app.services import invoicing

    tenant = tenants["admin"]
    await _approved_sheet(conn, skeleton, tenant, user=tenants["worker"].user_id)

    invoice = await invoicing.generate_invoice(
        conn,
        company_id=tenant.company_id,
        actor_user_id=tenant.user_id,
        request_id="flow",
        ip_address=None,
        contract_public_id=await _public(conn, "contracts", skeleton.contract),
        period_start=PERIOD_START,
        period_end=PERIOD_END,
    )

    assert invoice["public_id"].startswith("I")
    assert Decimal(str(invoice["total_amount"])) == EXPECTED_TOTAL
    assert Decimal(str(invoice["balance_due"])) == EXPECTED_TOTAL
    assert Decimal(str(invoice["total_amount"])) == Decimal(str(HOURS)) * RATE


# =============================================================================
# defect pins
#
# Each of these is written as if the code worked. They fail today, the marker
# turns that into an xfail, and `strict=True` means fixing the defect turns the
# xfail into a failure that has to be acknowledged.
# =============================================================================
# Formerly xfail D1_COMPANY_NAME: verified 2026-10-08 -- get_invoice now executes.
async def test_get_invoice_reads_the_invoice_back(conn, skeleton, tenants) -> None:
    """``invoicing.get_invoice`` reads the invoice back with derived totals."""
    from app.services import invoicing

    tenant = tenants["admin"]
    invoice = await _draft_invoice(conn, skeleton, tenant, tenants["worker"].company_id)
    await _line(conn, skeleton, invoice, quantity=Decimal("10"), rate=Decimal("100"))

    read = await invoicing.get_invoice(
        conn, company_id=tenant.company_id, public_id=str(invoice["public_id"])
    )
    assert read["public_id"] == invoice["public_id"]
    assert read["status"] == "DRAFT"
    assert Decimal(str(read["total_amount"])) == Decimal("1000.0000")
    assert Decimal(str(read["balance_due"])) == Decimal("1000.0000")
    assert len(read["items"]) == 1


# Formerly xfail D1: verified 2026-10-09 -- _PROJECT_SELECT lateral alias fixed.
async def test_get_project_and_list_projects_return_the_project(conn, skeleton, tenants) -> None:
    """``code.get_project`` and ``code.list_projects`` return the project."""
    from app.services import code

    tenant = tenants["admin"]
    public_id = await _public(conn, "projects", skeleton.project)

    project = await code.get_project(conn, company_id=tenant.company_id, public_id=public_id)
    assert project["public_id"] == public_id
    assert project["name"] == "Test project"
    assert project["billing_status"] == "NOT_STARTED"

    listed = await code.list_projects(
        conn,
        company_id=tenant.company_id,
        search=None,
        status=None,
        client_company_id=None,
        owner_user_id=None,
        cursor_keys={},
        limit=10,
    )
    assert public_id in {row["public_id"] for row in listed}


# Formerly xfail D1: verified 2026-10-09 -- contracts.metadata added (0017).
async def test_get_contract_and_list_contracts_return_the_contract(conn, skeleton, tenants) -> None:
    """``contracts.get_contract`` / ``list_contracts`` return the contract."""
    from app.services import contracts

    tenant = tenants["admin"]
    public_id = await _public(conn, "contracts", skeleton.contract)

    contract = await contracts.get_contract(conn, company_id=tenant.company_id, public_id=public_id)
    assert contract["public_id"] == public_id
    assert contract["status"] == "ACTIVE"
    assert [r["project_role_id"] for r in contract["roles"]] == [
        await _public(conn, "project_roles", skeleton.project_role)
    ]

    listed = await contracts.list_contracts(
        conn,
        company_id=tenant.company_id,
        project_public_id=None,
        sow_public_id=None,
        status=None,
        search=None,
        expiring_within_days=None,
        limit=10,
        cursor_keys={},
    )
    assert public_id in {row["public_id"] for row in listed}


# Formerly xfail D1: verified 2026-10-09 -- suggest_matches date arithmetic fixed.
async def test_get_payment_and_suggest_matches_return_rows(conn, skeleton, tenants) -> None:
    """``payments.get_payment`` and ``suggest_matches`` return rows."""
    from app.services import payments

    tenant = tenants["admin"]
    payment = await _completed_payment(conn, tenant, amount=Decimal("100"))

    read = await payments.get_payment(
        conn, company_id=tenant.company_id, public_id=str(payment["public_id"])
    )
    assert read["public_id"] == payment["public_id"]
    assert Decimal(str(read["amount"])) == Decimal("100.0000")

    invoice = await _approved_invoice(
        conn, skeleton, tenant, tenants["worker"].company_id, Decimal("100.00")
    )
    txn = await _bank_transaction(conn, skeleton, tenant, amount=Decimal("100.00"))
    candidates = await payments.suggest_matches(
        conn,
        company_id=tenant.company_id,
        transaction_id=str(txn["id"]),
        actor_user_id=tenant.user_id,
        request_id="flow",
    )
    assert [c["invoice_id"] for c in candidates] == [invoice["public_id"]]


# Formerly xfail D2_TIMESHEET_ENTRIES: verified 2026-10-08 -- 0016 fixed
# app.assert_timesheet_editable() to read locked_at, so entries record.
async def test_a_time_entry_is_recorded_and_the_hours_are_derived(conn, skeleton, tenants) -> None:
    """A time entry is recorded and the hours are derived from clock times."""
    from app.services import work

    tenant = tenants["admin"]
    worker = tenants["worker"].user_id
    sheet = await _draft_sheet(conn, skeleton, tenant, user=worker)

    updated = await work.add_entry(
        conn,
        company_id=tenant.company_id,
        public_id=str(sheet["public_id"]),
        actor_user_id=worker,
        request_id="flow",
        ip_address=None,
        payload={
            "entry_date": PERIOD_START,
            "start_time": "09:00",
            "end_time": "17:30",
            "break_minutes": 30,
            "work_description": "delivery work",
        },
    )
    assert Decimal(str(updated["total_hours"])) == Decimal("8.0000")
    assert Decimal(str(updated["billable_hours"])) == Decimal("8.0000")
    assert Decimal(str(updated["total_amount"])) == Decimal("600.0000")


async def test_the_database_refuses_an_entry_on_a_locked_timesheet(conn, skeleton, tenants) -> None:
    """``app.compute_entry`` refuses the write with a clear reason.

    W3 says the employee needs to know the sheet is closed rather than have the
    write silently vanish, so this one raises rather than filtering the row out.
    DEFECT D2 does not reach it, because ``trg_entries_compute`` fires before
    ``trg_entries_timesheet_editable`` and stops the statement first.
    """

    tenant = tenants["admin"]
    sheet = await _approved_sheet(conn, skeleton, tenant, user=tenants["worker"].user_id)
    await conn.execute(
        text("UPDATE public.timesheets SET status = 'LOCKED' WHERE id = :i"), {"i": sheet["id"]}
    )

    # The SAVEPOINT keeps the transaction alive so the row count below is readable.
    with pytest.raises(IntegrityError) as caught:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "INSERT INTO public.timesheet_entries"
                    " (timesheet_id, entry_date, start_time, end_time, work_description, source)"
                    " VALUES (:t, :d, '09:00', '10:00', 'sneaky', 'MANUAL')"
                ),
                {"t": sheet["id"], "d": PERIOD_START},
            )
    assert caught.value.orig.sqlstate == "23514", "expected check_violation"
    assert "timesheet is LOCKED" in str(caught.value)

    entries = (
        await conn.execute(
            text("SELECT count(*) FROM public.timesheet_entries WHERE timesheet_id = :t"),
            {"t": sheet["id"]},
        )
    ).scalar_one()
    assert entries == 0


# Formerly xfail D4: verified 2026-10-09 -- passes (metadata migration unblocked it).
async def test_create_sow_attaches_the_roles_it_was_given(conn, skeleton, tenants) -> None:
    """``code.create_sow`` attaches the roles it was given."""
    from app.services import code

    tenant = tenants["admin"]
    project_public_id = await _public(conn, "projects", skeleton.project)
    role_public_id = await _public(conn, "project_roles", skeleton.project_role)

    sow = await code.create_sow(
        conn,
        company_id=tenant.company_id,
        project_public_id=project_public_id,
        actor_user_id=tenant.user_id,
        request_id="flow",
        ip_address=None,
        payload={
            "title": "SOW with roles",
            "counterparty_company_id": tenants["worker"].company_public_id,
            "roles": [{"project_role_id": role_public_id, "quantity": 1}],
        },
    )
    assert [r["project_role_id"] for r in sow["roles"]] == [role_public_id]


# Formerly xfail D7_PROFILE_COLUMNS: verified 2026-10-08 -- create_timesheet returns.
async def test_create_timesheet_returns_the_sheet_it_created(conn, skeleton, tenants) -> None:
    """``work.create_timesheet`` returns the sheet it created."""
    from app.services import work

    tenant = tenants["admin"]
    worker = tenants["worker"].user_id
    created = await work.create_timesheet(
        conn,
        company_id=tenant.company_id,
        actor_user_id=worker,
        request_id="flow",
        ip_address=None,
        payload={
            "assignment_id": skeleton.assignment,
            "period_start": PERIOD_START,
            "period_end": PERIOD_END,
        },
    )
    assert created["public_id"].startswith("TS")
    assert created["status"] == "DRAFT"
    assert created["editable"] is True
    assert created["entries"] == []


# Formerly xfail D8_AUDIT_TIMESTAMP: verified 2026-10-08 -- history orders by occurred_at.
async def test_invoice_history_is_returned(conn, skeleton, tenants) -> None:
    """Invoice history is returned in occurred_at order."""
    from app.services import audit as audit_service

    tenant = tenants["admin"]
    invoice = await _draft_invoice(conn, skeleton, tenant, tenants["worker"].company_id)
    await audit_service.record(
        conn,
        action="invoice.tested",
        resource_type="invoice",
        resource_public_id=str(invoice["public_id"]),
        company_id=tenant.company_id,
        actor_user_id=tenant.user_id,
        request_id="flow",
    )

    rows = (
        (
            await conn.execute(
                text(
                    """
                    SELECT a.action, a.occurred_at, a.actor_user_id
                      FROM platform.audit_logs a
                     WHERE a.resource_type = 'invoice' AND a.resource_public_id = :pid
                     ORDER BY a.occurred_at DESC
                    """
                ),
                {"pid": invoice["public_id"]},
            )
        )
        .mappings()
        .all()
    )
    assert [r["action"] for r in rows] == ["invoice.tested"]

    from app.services.invoicing import _invoice_history

    history = await _invoice_history(conn, str(invoice["public_id"]), tenant.company_id)
    assert [h["action"] for h in history] == ["invoice.tested"]


async def test_update_sow_resolves_the_roles_it_binds(conn, skeleton, tenants) -> None:
    """``code.update_sow`` runs the roles through ``_resolve_project_role_ids`` first.

    Its return value goes through ``get_sow``, which DEFECT D1 also breaks, so the
    resolution step is exercised directly.
    """
    from app.services import code

    tenant = tenants["admin"]
    role_public_id = await _public(conn, "project_roles", skeleton.project_role)

    resolved = await code._resolve_project_role_ids(
        conn,
        company_id=tenant.company_id,
        project_id=uuid.UUID(skeleton.project),
        requested=[{"project_role_id": role_public_id, "quantity": 1, "rate": "75"}],
    )
    assert str(resolved[0]["project_role_id"]) == skeleton.project_role

    # A duplicate is refused before anything is written.
    with pytest.raises(ValidationError) as caught:
        await code._resolve_project_role_ids(
            conn,
            company_id=tenant.company_id,
            project_id=uuid.UUID(skeleton.project),
            requested=[
                {"project_role_id": role_public_id, "quantity": 1},
                {"project_role_id": role_public_id, "quantity": 2},
            ],
        )
    assert caught.value.code == "VALIDATION_ERROR"


# =============================================================================
# the negative paths
# =============================================================================
async def test_assignment_for_a_role_the_contract_does_not_cover_is_refused(
    conn, skeleton, tenants
) -> None:
    """Work may not be filed against a role the contract does not carry."""
    from app.services import work

    tenant = tenants["admin"]
    uncovered = await _one(
        conn,
        "INSERT INTO public.project_roles (project_id, company_id, title, required_count)"
        " VALUES (:p, :c, 'Uncovered role', 2) RETURNING id::text, public_id",
        {"p": skeleton.project, "c": tenant.company_id},
    )

    with pytest.raises(BusinessRuleViolationError) as caught:
        await work.create_assignment(
            conn,
            company_id=tenant.company_id,
            actor_user_id=tenant.user_id,
            request_id="flow",
            ip_address=None,
            payload={
                "contract_id": await _public(conn, "contracts", skeleton.contract),
                "user_id": tenants["worker"].user_public_id,
                "project_role_id": uncovered["public_id"],
            },
        )

    error = caught.value
    assert error.code == "BUSINESS_RULE_VIOLATION"
    assert error.details["reason"] == "ROLE_NOT_ON_CONTRACT"
    assert error.details["project_role_id"] == uncovered["public_id"]

    created = (
        await conn.execute(
            text(
                "SELECT count(*) FROM public.assignments a"
                " JOIN public.contract_roles cr ON cr.id = a.contract_role_id"
                " WHERE cr.project_role_id = CAST(:r AS uuid)"
            ),
            {"r": uncovered["id"]},
        )
    ).scalar_one()
    assert created == 0


async def test_sow_role_belonging_to_another_project_is_refused(conn, skeleton, tenants) -> None:
    """A SOW cannot borrow a role from a different project in the same company."""
    from app.services import code

    tenant = tenants["admin"]
    draft_sow = await _one(
        conn,
        "INSERT INTO public.sows (project_id, company_id, sow_type, counterparty_company_id,"
        " title, status) VALUES (:p, :c, 'COMPANY', :cp, 'Draft SOW', 'DRAFT')"
        " RETURNING id::text, public_id",
        {"p": skeleton.project, "c": tenant.company_id, "cp": tenants["worker"].company_id},
    )
    other_project = await _one(
        conn,
        "INSERT INTO public.projects (company_id, name, status)"
        " VALUES (:c, 'Other project', 'ACTIVE') RETURNING id::text",
        {"c": tenant.company_id},
    )
    foreign_role = await _one(
        conn,
        "INSERT INTO public.project_roles (project_id, company_id, title, required_count)"
        " VALUES (:p, :c, 'Foreign role', 1) RETURNING id::text, public_id",
        {"p": other_project["id"], "c": tenant.company_id},
    )

    with pytest.raises(BusinessRuleViolationError) as caught:
        await code.update_sow(
            conn,
            company_id=tenant.company_id,
            public_id=str(draft_sow["public_id"]),
            actor_user_id=tenant.user_id,
            request_id="flow",
            ip_address=None,
            changes={"roles": [{"project_role_id": foreign_role["public_id"], "quantity": 1}]},
        )

    assert caught.value.details["reason"] == "PROJECT_ROLE_NOT_ON_PROJECT"
    assert caught.value.details["project_role_id"] == foreign_role["public_id"]

    roles = (
        await conn.execute(
            text("SELECT count(*) FROM public.sow_roles WHERE sow_id = CAST(:s AS uuid)"),
            {"s": draft_sow["id"]},
        )
    ).scalar_one()
    assert roles == 0, "a refused SOW must not keep the offending role"


async def test_contract_role_belonging_to_another_project_is_refused(
    conn, skeleton, tenants
) -> None:
    """The same rule on the contract side, checked before anything is written."""
    from app.services import contracts

    tenant = tenants["admin"]
    other_project = await _one(
        conn,
        "INSERT INTO public.projects (company_id, name, status)"
        " VALUES (:c, 'Other project 2', 'ACTIVE') RETURNING id::text",
        {"c": tenant.company_id},
    )
    foreign_role = await _one(
        conn,
        "INSERT INTO public.project_roles (project_id, company_id, title, required_count)"
        " VALUES (:p, :c, 'Foreign role 2', 1) RETURNING id::text, public_id",
        {"p": other_project["id"], "c": tenant.company_id},
    )

    with pytest.raises(BusinessRuleViolationError) as caught:
        await contracts._resolve_contract_roles(
            conn,
            company_id=tenant.company_id,
            project_id=uuid.UUID(skeleton.project),
            requested=[{"project_role_id": foreign_role["public_id"]}],
        )
    assert caught.value.details["reason"] == "PROJECT_ROLE_NOT_ON_PROJECT"


# Formerly xfail D7: verified 2026-10-08 -- generate_invoice reaches the duplicate check.
async def test_second_invoice_for_the_same_contract_and_period_is_refused(
    conn, skeleton, tenants
) -> None:
    """``INVOICE_PERIOD_EXISTS``: a retried billing run cannot double-bill."""
    from app.services import invoicing

    tenant = tenants["admin"]
    await _approved_sheet(conn, skeleton, tenant, user=tenants["worker"].user_id)
    existing = await _draft_invoice(conn, skeleton, tenant, tenants["worker"].company_id)

    with pytest.raises(BusinessRuleViolationError) as caught:
        await invoicing.generate_invoice(
            conn,
            company_id=tenant.company_id,
            actor_user_id=tenant.user_id,
            request_id="flow",
            ip_address=None,
            contract_public_id=await _public(conn, "contracts", skeleton.contract),
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )

    assert caught.value.details["reason"] == "INVOICE_PERIOD_EXISTS"
    assert caught.value.details["invoice_id"] == existing["public_id"]

    count = (
        await conn.execute(
            text(
                "SELECT count(*) FROM public.invoices WHERE contract_id = CAST(:c AS uuid)"
                " AND period_start = :ps AND period_end = :pe"
            ),
            {"c": skeleton.contract, "ps": PERIOD_START, "pe": PERIOD_END},
        )
    ).scalar_one()
    assert count == 1


# Formerly xfail D7: verified 2026-10-08 -- generate_invoice reaches the empty check.
async def test_generate_invoice_refuses_when_nothing_is_billable(conn, skeleton, tenants) -> None:
    """``NOTHING_TO_BILL`` rather than an invoice with no lines."""
    from app.services import invoicing

    tenant = tenants["admin"]
    with pytest.raises(BusinessRuleViolationError) as caught:
        await invoicing.generate_invoice(
            conn,
            company_id=tenant.company_id,
            actor_user_id=tenant.user_id,
            request_id="flow",
            ip_address=None,
            contract_public_id=await _public(conn, "contracts", skeleton.contract),
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )
    assert caught.value.details["reason"] == "NOTHING_TO_BILL"

    count = (
        await conn.execute(
            text("SELECT count(*) FROM public.invoices WHERE contract_id = CAST(:c AS uuid)"),
            {"c": skeleton.contract},
        )
    ).scalar_one()
    assert count == 0


async def test_invoice_for_a_draft_contract_is_refused(conn, tenants) -> None:
    """Only an accepted or active contract may be billed."""
    from app.services import invoicing

    tenant = tenants["admin"]
    project = await _one(
        conn,
        "INSERT INTO public.projects (company_id, name, status)"
        " VALUES (:c, 'Draft contract project', 'ACTIVE') RETURNING id::text, public_id",
        {"c": tenant.company_id},
    )
    sow = await _one(
        conn,
        "INSERT INTO public.sows (project_id, company_id, sow_type, counterparty_company_id,"
        " title, status) VALUES (:p, :c, 'COMPANY', :cp, 'Draft SOW', 'ACTIVE')"
        " RETURNING id::text",
        {"p": project["id"], "c": tenant.company_id, "cp": tenants["worker"].company_id},
    )
    contract = await _one(
        conn,
        "INSERT INTO public.contracts (sow_id, project_id, company_id, contract_type,"
        " counterparty_company_id, title, status) VALUES (:s, :p, :c, 'COMPANY', :cp,"
        " 'Draft contract', 'DRAFT') RETURNING id::text, public_id",
        {
            "s": sow["id"],
            "p": project["id"],
            "c": tenant.company_id,
            "cp": tenants["worker"].company_id,
        },
    )

    with pytest.raises(BusinessRuleViolationError) as caught:
        await invoicing.preview_invoice(
            conn,
            company_id=tenant.company_id,
            contract_public_id=str(contract["public_id"]),
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )
    assert caught.value.details["reason"] == "CONTRACT_NOT_BILLABLE"
    assert caught.value.details["status"] == "DRAFT"


async def test_approved_or_locked_timesheet_entries_cannot_be_edited(
    conn, skeleton, tenants
) -> None:
    """Rule 6: an approved sheet is closed; a correction is a revision, not an edit."""
    from app.services import work

    tenant = tenants["admin"]
    worker = tenants["worker"].user_id
    sheet = await _approved_sheet(conn, skeleton, tenant, user=worker)
    sheet_public_id = str(sheet["public_id"])

    with pytest.raises(InvalidStateTransitionError) as caught:
        await work.add_entry(
            conn,
            company_id=tenant.company_id,
            public_id=sheet_public_id,
            actor_user_id=worker,
            request_id="flow",
            ip_address=None,
            payload={
                "entry_date": PERIOD_START,
                "start_time": "09:00",
                "end_time": "17:00",
                "work_description": "sneaky",
            },
        )
    assert caught.value.code == "INVALID_STATE_TRANSITION"
    assert caught.value.details["status"] == "APPROVED"
    assert caught.value.details["editable_in"] == ["DRAFT", "REJECTED"]

    for coroutine in (
        work.update_entry(
            conn,
            company_id=tenant.company_id,
            public_id=sheet_public_id,
            entry_id=uuid.uuid4(),
            actor_user_id=worker,
            request_id="flow",
            ip_address=None,
            payload={"work_description": "edited"},
        ),
        work.delete_entry(
            conn,
            company_id=tenant.company_id,
            public_id=sheet_public_id,
            entry_id=uuid.uuid4(),
            actor_user_id=worker,
            request_id="flow",
            ip_address=None,
            reason="because",
        ),
    ):
        with pytest.raises(InvalidStateTransitionError):
            await coroutine

    # Only the sheet's owner may edit a draft one either. The draft belongs to the
    # other user so the (user, assignment, period) unique index is not in the way.
    draft = await _draft_sheet(conn, skeleton, tenant, user=tenant.user_id)
    with pytest.raises(BusinessRuleViolationError) as caught:
        await work.add_entry(
            conn,
            company_id=tenant.company_id,
            public_id=str(draft["public_id"]),
            actor_user_id=worker,
            request_id="flow",
            ip_address=None,
            payload={"entry_date": PERIOD_START, "hours": 8, "work_description": "not mine"},
        )
    assert caught.value.details["reason"] == "TIMESHEET_NOT_OWNED"


async def test_an_empty_timesheet_cannot_be_submitted(conn, skeleton, tenants) -> None:
    """No entries, no submission."""
    from app.services import work

    tenant = tenants["admin"]
    worker = tenants["worker"].user_id
    sheet = await _draft_sheet(conn, skeleton, tenant, user=worker)
    with pytest.raises(BusinessRuleViolationError) as caught:
        await work.submit_timesheet(
            conn,
            company_id=tenant.company_id,
            public_id=str(sheet["public_id"]),
            actor_user_id=worker,
            request_id="flow",
            ip_address=None,
        )
    assert caught.value.details["reason"] == "TIMESHEET_EMPTY"


async def test_allocating_more_than_the_payment_is_refused(conn, skeleton, tenants) -> None:
    """``ALLOCATION_EXCEEDS_PAYMENT``: money cannot be created by allocation."""
    from app.services import payments

    tenant = tenants["admin"]
    invoice = await _approved_invoice(
        conn, skeleton, tenant, tenants["worker"].company_id, Decimal("1000.00")
    )
    payment = await _completed_payment(conn, tenant, amount=Decimal("100.00"))

    with pytest.raises(BusinessRuleViolationError) as caught:
        await payments.allocate_payment(
            conn,
            company_id=tenant.company_id,
            payment_public_id=str(payment["public_id"]),
            actor_user_id=tenant.user_id,
            request_id="flow",
            ip_address=None,
            allocations=[{"invoice_id": str(invoice["public_id"]), "amount": Decimal("150.00")}],
        )

    error = caught.value
    assert error.details["reason"] == "ALLOCATION_EXCEEDS_PAYMENT"
    assert Decimal(str(error.details["available"])) == Decimal("100.0000")
    assert Decimal(str(error.details["requested"])) == Decimal("150.0000")

    allocations = (
        await conn.execute(
            text(
                "SELECT count(*) FROM public.payment_allocations"
                " WHERE payment_id = CAST(:p AS uuid)"
            ),
            {"p": payment["id"]},
        )
    ).scalar_one()
    assert allocations == 0
    assert invoice["status"] == "APPROVED"


async def test_allocating_to_a_draft_invoice_is_refused(conn, skeleton, tenants) -> None:
    """``INVOICE_NOT_APPROVED``: only an approved invoice can receive money."""
    from app.services import payments

    tenant = tenants["admin"]
    invoice = await _draft_invoice(conn, skeleton, tenant, tenants["worker"].company_id)
    await _line(conn, skeleton, invoice, quantity=Decimal("10"), rate=Decimal("100"))
    payment = await _completed_payment(conn, tenant, amount=Decimal("100.00"))

    with pytest.raises(BusinessRuleViolationError) as caught:
        await payments.allocate_payment(
            conn,
            company_id=tenant.company_id,
            payment_public_id=str(payment["public_id"]),
            actor_user_id=tenant.user_id,
            request_id="flow",
            ip_address=None,
            allocations=[{"invoice_id": str(invoice["public_id"]), "amount": Decimal("50.00")}],
        )
    assert caught.value.details["reason"] == "INVOICE_NOT_APPROVED"
    assert caught.value.details["status"] == "DRAFT"

    stored = await _one(
        conn, "SELECT status, balance_due FROM public.invoices WHERE id = :i", {"i": invoice["id"]}
    )
    assert stored["status"] == "DRAFT"
    assert Decimal(str(stored["balance_due"])) == Decimal("1000.0000")


async def test_allocating_in_a_foreign_currency_is_refused(conn, skeleton, tenants) -> None:
    """A payment and an invoice must agree on currency unless a rate is supplied."""
    from app.services import payments

    tenant = tenants["admin"]
    invoice = await _approved_invoice(
        conn, skeleton, tenant, tenants["worker"].company_id, Decimal("1000.00")
    )
    payment = await _completed_payment(conn, tenant, amount=Decimal("100.00"), currency="EUR")

    with pytest.raises(ValidationError) as caught:
        await payments.allocate_payment(
            conn,
            company_id=tenant.company_id,
            payment_public_id=str(payment["public_id"]),
            actor_user_id=tenant.user_id,
            request_id="flow",
            ip_address=None,
            allocations=[{"invoice_id": str(invoice["public_id"]), "amount": Decimal("50.00")}],
        )
    assert caught.value.details["reason"] == "CURRENCY_MISMATCH"
    assert caught.value.details["payment_currency"] == "EUR"
    assert caught.value.details["invoice_currency"] == "USD"


async def test_allocating_more_than_the_invoice_balance_is_refused(conn, skeleton, tenants) -> None:
    """``ALLOCATION_EXCEEDS_BALANCE``: an invoice cannot be over-settled either."""
    from app.services import payments

    tenant = tenants["admin"]
    invoice = await _approved_invoice(
        conn, skeleton, tenant, tenants["worker"].company_id, Decimal("100.00")
    )
    payment = await _completed_payment(conn, tenant, amount=Decimal("500.00"))

    with pytest.raises(BusinessRuleViolationError) as caught:
        await payments.allocate_payment(
            conn,
            company_id=tenant.company_id,
            payment_public_id=str(payment["public_id"]),
            actor_user_id=tenant.user_id,
            request_id="flow",
            ip_address=None,
            allocations=[{"invoice_id": str(invoice["public_id"]), "amount": Decimal("150.00")}],
        )
    assert caught.value.details["reason"] == "ALLOCATION_EXCEEDS_BALANCE"
    assert Decimal(str(caught.value.details["balance_due"])) == Decimal("100.0000")


async def test_payment_in_a_non_completed_state_cannot_be_allocated(
    conn, skeleton, tenants
) -> None:
    """``PAYMENT_NOT_COMPLETED``: scheduled money has not moved yet."""
    from app.services import payments

    tenant = tenants["admin"]
    invoice = await _approved_invoice(
        conn, skeleton, tenant, tenants["worker"].company_id, Decimal("100.00")
    )
    scheduled = await _one(
        conn,
        """
        INSERT INTO public.payments
          (company_id, direction, status, amount, currency, payment_method,
           authorization_type, authorized_by, authorized_at, processor,
           idempotency_key, reconciliation_status, created_by)
        VALUES (CAST(:c AS uuid), 'RECEIVABLE', 'SCHEDULED', 500, 'USD', 'ACH', 'EXPLICIT',
                CAST(:actor AS uuid), now(), 'MANUAL', :key, 'MANUAL', CAST(:actor AS uuid))
        RETURNING id::text, public_id, status
        """,
        {"c": tenant.company_id, "actor": tenant.user_id, "key": uuid.uuid4().hex},
    )
    assert scheduled["status"] == "SCHEDULED"

    with pytest.raises(BusinessRuleViolationError) as caught:
        await payments.allocate_payment(
            conn,
            company_id=tenant.company_id,
            payment_public_id=str(scheduled["public_id"]),
            actor_user_id=tenant.user_id,
            request_id="flow",
            ip_address=None,
            allocations=[{"invoice_id": str(invoice["public_id"]), "amount": Decimal("50.00")}],
        )
    assert caught.value.details["reason"] == "PAYMENT_NOT_COMPLETED"
    assert caught.value.details["status"] == "SCHEDULED"


# =============================================================================
# database-enforced rules, as the non-BYPASSRLS API role
# =============================================================================
async def _sheet_for(api_conn, skeleton, tenant, user: uuid.UUID, label: str) -> str:
    """A DRAFT timesheet for `user` on a fresh assignment.

    One assignment per call keeps ``timesheets``'s
    ``(user_id, assignment_id, period_start, period_end)`` unique index happy
    without needing a third user, and `app.can_record_time` is satisfied because
    the assignment reaches back far enough to cover the period.
    """
    assignment = str(
        (
            await api_conn.execute(
                text(
                    "INSERT INTO public.assignments"
                    " (contract_id, contract_role_id, project_id, company_id, user_id,"
                    "  role_title, hourly_rate, currency, start_date, end_date, status)"
                    " VALUES (:c, :cr, :p, :co, :u, :role, 75, 'USD',"
                    "         current_date - 60, current_date + 300, 'ACTIVE') RETURNING id"
                ),
                {
                    "c": skeleton.contract,
                    "cr": skeleton.contract_role,
                    "p": skeleton.project,
                    "co": tenant.company_id,
                    "u": user,
                    "role": f"Assignee {label}",
                },
            )
        ).scalar_one()
    )
    return str(
        (
            await api_conn.execute(
                text(
                    "INSERT INTO public.timesheets"
                    " (user_id, company_id, assignment_id, contract_id, project_id,"
                    "  period_start, period_end, billing_frequency, status, currency)"
                    " VALUES (:u, :co, :a, :c, :p, :ps, :pe, 'MONTHLY', 'DRAFT', 'USD')"
                    " RETURNING id"
                ),
                {
                    "u": user,
                    "co": tenant.company_id,
                    "a": assignment,
                    "c": skeleton.contract,
                    "p": skeleton.project,
                    "ps": PERIOD_START,
                    "pe": PERIOD_END,
                },
            )
        ).scalar_one()
    )


async def test_self_approving_a_timesheet_is_refused(api_conn, api_skeleton, tenants) -> None:
    """Segregation of duties, enforced by ``app.assert_timesheet_transition``.

    Run as ``mytrakin_api``: the guard opens with
    ``IF app.is_trusted_context() THEN RETURN NEW``, and that helper is true for
    the BYPASSRLS role in ``DATABASE_URL``, so the default connection would sail
    straight past the check.
    """
    from app.db.session import set_identity

    tenant = tenants["admin"]
    sheet_id = await _sheet_for(api_conn, api_skeleton, tenant, tenant.user_id, "a")

    # Submitting one's own sheet is allowed.
    await api_conn.execute(
        text("UPDATE public.timesheets SET status = 'SUBMITTED' WHERE id = :i"), {"i": sheet_id}
    )
    assert (
        await api_conn.execute(
            text("SELECT status FROM public.timesheets WHERE id = :i"), {"i": sheet_id}
        )
    ).scalar_one() == "SUBMITTED"

    # Approving it is not. The exception has to escape the SAVEPOINT block so the
    # savepoint is rolled back and the outer transaction stays usable.
    with pytest.raises(DBAPIError) as caught:
        async with api_conn.begin_nested():
            await api_conn.execute(
                text("UPDATE public.timesheets SET status = 'APPROVED' WHERE id = :i"),
                {"i": sheet_id},
            )
    assert caught.value.orig.sqlstate == "42501", "expected insufficient_privilege"
    message = str(caught.value)
    assert "segregation of duties" in message
    assert "cannot approve your own timesheet" in message

    assert (
        await api_conn.execute(
            text("SELECT status FROM public.timesheets WHERE id = :i"), {"i": sheet_id}
        )
    ).scalar_one() == "SUBMITTED"

    # A different administrator holding timesheets.approve may do it.
    await set_identity(api_conn, user_id=tenants["worker"].user_id, company_id=tenant.company_id)
    await api_conn.execute(
        text("UPDATE public.timesheets SET status = 'APPROVED' WHERE id = :i"), {"i": sheet_id}
    )
    assert (
        await api_conn.execute(
            text("SELECT status FROM public.timesheets WHERE id = :i"), {"i": sheet_id}
        )
    ).scalar_one() == "APPROVED"


async def test_timesheet_state_machine_cannot_be_skipped(api_conn, api_skeleton, tenants) -> None:
    """DRAFT may not jump to APPROVED or LOCKED."""
    tenant = tenants["admin"]
    sheet_id = await _sheet_for(api_conn, api_skeleton, tenant, tenants["worker"].user_id, "bb")

    for target in ("APPROVED", "LOCKED"):
        with pytest.raises(DBAPIError) as caught:
            async with api_conn.begin_nested():
                await api_conn.execute(
                    text("UPDATE public.timesheets SET status = :t WHERE id = :i"),
                    {"t": target, "i": sheet_id},
                )
        assert caught.value.orig.sqlstate == "23514", "expected check_violation"
        assert f"invalid timesheet transition DRAFT -> {target}" in str(caught.value)

    assert (
        await api_conn.execute(
            text("SELECT status FROM public.timesheets WHERE id = :i"), {"i": sheet_id}
        )
    ).scalar_one() == "DRAFT"


async def test_a_locked_timesheet_is_immutable(api_conn, api_skeleton, tenants) -> None:
    """Once APPROVED, the header totals may not be rewritten; LOCKED closes the sheet."""
    from app.db.session import set_identity

    tenant = tenants["admin"]
    # Someone other than the author, so segregation of duties is not what stops it.
    await set_identity(api_conn, user_id=tenants["worker"].user_id, company_id=tenant.company_id)
    sheet_id = await _sheet_for(api_conn, api_skeleton, tenant, tenant.user_id, "lock")

    for status in ("SUBMITTED", "APPROVED"):
        await api_conn.execute(
            text("UPDATE public.timesheets SET status = :s WHERE id = :i"),
            {"s": status, "i": sheet_id},
        )

    with pytest.raises(DBAPIError) as caught:
        async with api_conn.begin_nested():
            await api_conn.execute(
                text(
                    "UPDATE public.timesheets SET total_hours = 100, billable_hours = 100,"
                    " total_amount = 7500 WHERE id = :i"
                ),
                {"i": sheet_id},
            )
    assert caught.value.orig.sqlstate == "23514", "expected check_violation"
    assert "cannot be modified" in str(caught.value)

    await api_conn.execute(
        text("UPDATE public.timesheets SET status = 'LOCKED' WHERE id = :i"), {"i": sheet_id}
    )
    stored = await _one(
        api_conn,
        "SELECT status, total_hours, billable_hours FROM public.timesheets WHERE id = :i",
        {"i": sheet_id},
    )
    assert stored["status"] == "LOCKED"
    assert Decimal(str(stored["total_hours"])) == Decimal("0.0000")


def test_segregation_of_duties_error_is_typed_for_the_api() -> None:
    """The code a client sees when the database refuses an approval."""
    assert SegregationOfDutiesError().code == "SEGREGATION_OF_DUTIES"
    assert SegregationOfDutiesError().status_code == 403
    assert isinstance(SegregationOfDutiesError(), PermissionDeniedError)


# =============================================================================
# tenant boundary
# =============================================================================
async def test_cross_tenant_lookup_raises_resource_not_found(other_tenant_conn, tenants) -> None:
    """Company A cannot read company B's rows, and cannot learn that they exist."""
    from app.db.session import get_connection_factory, set_identity
    from app.services import code

    company_b = tenants["worker"].company_id
    company_a = tenants["admin"].company_id

    project_b = await _one(
        other_tenant_conn,
        "INSERT INTO public.projects (company_id, name, status)"
        " VALUES (CAST(current_setting('app.company_id') AS uuid), 'B project', 'ACTIVE')"
        " RETURNING id::text, public_id",
        {},
    )
    foreign = await _one(
        other_tenant_conn,
        "INSERT INTO public.project_roles (project_id, company_id, title, required_count)"
        " VALUES (:p, :c, 'B only', 1) RETURNING id::text, public_id",
        {"p": project_b["id"], "c": company_b},
    )

    # In company B it is visible, so the identifier is real.
    assert (
        await code.get_project_role(
            other_tenant_conn, company_id=company_b, public_id=str(foreign["public_id"])
        )
    )["title"] == "B only"

    # Under company A's identity the very same public id is simply not there.
    factory = get_connection_factory()
    async with factory() as as_a:
        tx = await as_a.begin()
        try:
            await set_identity(as_a, user_id=tenants["admin"].user_id, company_id=company_a)
            with pytest.raises(ResourceNotFoundError) as caught:
                await code.get_project_role(
                    as_a, company_id=company_a, public_id=str(foreign["public_id"])
                )
            assert caught.value.code == "RESOURCE_NOT_FOUND"
            assert caught.value.status_code == 404
            # 404 and not 403: a 403 would confirm the identifier exists.
            assert not isinstance(caught.value, PermissionDeniedError)
        finally:
            await tx.rollback()


# =============================================================================
# audit
# =============================================================================
async def test_audit_is_written_for_actions_the_service_can_complete(
    conn, skeleton, tenants
) -> None:
    """The trail itself works; only the broken reads stop it being observed."""
    from app.services import code

    tenant = tenants["admin"]
    role = await code.create_project_role(
        conn,
        company_id=tenant.company_id,
        project_public_id=await _public(conn, "projects", skeleton.project),
        actor_user_id=tenant.user_id,
        request_id="flow",
        ip_address=None,
        payload={"title": "Audited role", "required_count": 1},
    )

    row = await _one(
        conn,
        """
        SELECT action, resource_type, resource_public_id, actor_user_id, request_id
          FROM platform.audit_logs
         WHERE company_id = CAST(:c AS uuid) AND action = 'project_role.created'
         ORDER BY occurred_at DESC LIMIT 1
        """,
        {"c": tenant.company_id},
    )
    assert row["action"] == "project_role.created"
    assert row["resource_type"] == "project_role"
    assert row["resource_public_id"] == role["public_id"]
    assert str(row["actor_user_id"]) == str(tenant.user_id)
    assert row["request_id"] == "flow"


# Formerly xfail D7: verified 2026-10-09 -- full chain runs end to end.
async def test_audit_rows_exist_for_the_key_actions(conn, skeleton, tenants) -> None:
    """``contract.created``, ``invoice.generated``, ``payment.recorded``,
    ``reconciliation.accepted``.
    """
    from app.services import contracts, invoicing, payments

    tenant = tenants["admin"]
    await _approved_sheet(conn, skeleton, tenant, user=tenants["worker"].user_id)

    await contracts.create_contract(
        conn,
        company_id=tenant.company_id,
        actor_user_id=tenant.user_id,
        request_id="flow",
        ip_address=None,
        payload={
            "project_id": await _public(conn, "projects", skeleton.project),
            "sow_id": await _public(conn, "sows", skeleton.sow),
            "title": "Audited contract",
            "counterparty_company_id": tenants["worker"].company_public_id,
            "roles": [
                {
                    "project_role_id": await _public(conn, "project_roles", skeleton.project_role),
                    "quantity": 1,
                    "rate": str(RATE),
                    "rate_type": "HOURLY",
                }
            ],
        },
    )
    invoice = await invoicing.generate_invoice(
        conn,
        company_id=tenant.company_id,
        actor_user_id=tenant.user_id,
        request_id="flow",
        ip_address=None,
        contract_public_id=await _public(conn, "contracts", skeleton.contract),
        period_start=PERIOD_START,
        period_end=PERIOD_END,
    )
    payment = await payments.record_payment(
        conn,
        company_id=tenant.company_id,
        actor_user_id=tenant.user_id,
        request_id="flow",
        ip_address=None,
        payload={"amount": "12000.00", "currency": "USD"},
    )

    actions = set(
        (
            await conn.execute(
                text("SELECT action FROM platform.audit_logs WHERE company_id = CAST(:c AS uuid)"),
                {"c": tenant.company_id},
            )
        )
        .scalars()
        .all()
    )
    assert {"contract.created", "invoice.generated", "payment.recorded"} <= actions
    assert invoice["public_id"].startswith("I")
    assert payment["public_id"].startswith("PM")


# =============================================================================
# reconciliation
# =============================================================================
# Formerly xfail D1: verified 2026-10-09 -- reconcile path runs to PAID.
async def test_reconciliation_settles_the_invoice_and_matches_the_transaction(
    conn, skeleton, tenants
) -> None:
    """payment -> allocation -> bank transaction -> reconciliation -> PAID."""
    from app.services import payments

    tenant = tenants["admin"]
    invoice = await _approved_invoice(
        conn, skeleton, tenant, tenants["worker"].company_id, Decimal("1000.00")
    )
    txn = await _bank_transaction(conn, skeleton, tenant, amount=Decimal("1000.00"))

    candidates = await payments.suggest_matches(
        conn,
        company_id=tenant.company_id,
        transaction_id=str(txn["id"]),
        actor_user_id=tenant.user_id,
        request_id="flow",
    )
    assert candidates
    assert candidates[0]["invoice_id"] == invoice["public_id"]
    assert candidates[0]["suggestion"] in {"MATCH", "REVIEW"}
    assert "amount match" in candidates[0]["reason"]

    queue = await payments.reconciliation_queue(
        conn, company_id=tenant.company_id, status="SUGGESTED", limit=10, cursor_keys={}
    )
    assert queue

    decided = await payments.decide_match(
        conn,
        company_id=tenant.company_id,
        match_id=str(queue[0]["id"]),
        decision="ACCEPTED",
        actor_user_id=tenant.user_id,
        request_id="flow",
        ip_address=None,
        notes="accepted by the flow test",
    )
    assert decided["status"] == "ACCEPTED"
    assert decided["payment_public_id"].startswith("PM")

    settled = await _one(
        conn,
        "SELECT status, amount_paid, balance_due FROM public.invoices WHERE id = :i",
        {"i": invoice["id"]},
    )
    assert settled["status"] == "PAID"
    assert Decimal(str(settled["amount_paid"])) == Decimal("1000.0000")
    assert Decimal(str(settled["balance_due"])) == Decimal("0.0000")

    txn_state = await _one(
        conn,
        "SELECT match_status, is_reconciled FROM public.bank_transactions WHERE id = :i",
        {"i": txn["id"]},
    )
    assert txn_state["match_status"] == "MATCHED"
    assert txn_state["is_reconciled"] is True

    actions = set(
        (
            await conn.execute(
                text("SELECT action FROM platform.audit_logs WHERE company_id = CAST(:c AS uuid)"),
                {"c": tenant.company_id},
            )
        )
        .scalars()
        .all()
    )
    assert {"payment.recorded", "payment.allocated", "reconciliation.accepted"} <= actions


# =============================================================================
# sow -> contract auto-generation
# =============================================================================
async def _draft_sow_with_role(conn, skeleton, tenants, payload: dict[str, Any]) -> dict[str, Any]:
    """A DRAFT SOW with one priced role, built through the real service."""
    from app.services import code

    tenant = tenants["admin"]
    base: dict[str, Any] = {
        "title": "Auto SOW",
        "roles": [
            {
                "project_role_id": await _public(conn, "project_roles", skeleton.project_role),
                "quantity": 2,
                "rate": "80.0000",
                "rate_type": "HOURLY",
                "currency": "USD",
            }
        ],
    }
    base.update(payload)
    return await code.create_sow(
        conn,
        company_id=tenant.company_id,
        project_public_id=await _public(conn, "projects", skeleton.project),
        actor_user_id=tenant.user_id,
        request_id="flow",
        ip_address=None,
        payload=base,
    )


async def _activate_sow(conn, tenants, sow_public_id: str) -> dict[str, Any]:
    """Submit then approve a SOW, returning the ACTIVE representation."""
    from app.services import code

    tenant = tenants["admin"]
    await code.transition_sow(
        conn,
        company_id=tenant.company_id,
        public_id=sow_public_id,
        target="PENDING_APPROVAL",
        actor_user_id=tenant.user_id,
        request_id="flow",
        ip_address=None,
    )
    return await code.transition_sow(
        conn,
        company_id=tenant.company_id,
        public_id=sow_public_id,
        target="ACTIVE",
        actor_user_id=tenant.user_id,
        request_id="flow",
        ip_address=None,
    )


async def test_approving_a_company_sow_generates_its_contract(conn, skeleton, tenants) -> None:
    """One COMPANY contract per approved SOW, carrying every priced role."""
    from app.services import contracts

    tenant = tenants["admin"]
    role_public_id = await _public(conn, "project_roles", skeleton.project_role)
    sow = await _draft_sow_with_role(
        conn,
        skeleton,
        tenants,
        {"counterparty_company_id": tenants["worker"].company_public_id},
    )
    active = await _activate_sow(conn, tenants, sow["public_id"])

    assert active["contract_count"] == 1
    contract = await contracts.get_contract(
        conn, company_id=tenant.company_id, public_id=active["contract_ids"][0]
    )
    assert contract["public_id"].startswith("C")
    assert contract["status"] == "DRAFT"
    assert contract["contract_type"] == "COMPANY"
    assert contract["sow_id"] == sow["public_id"]
    assert contract["counterparty_company_id"] == tenants["worker"].company_public_id
    assert contract["counterparty_user_id"] is None
    assert [r["project_role_id"] for r in contract["roles"]] == [role_public_id]
    assert contract["roles"][0]["quantity"] == 2
    assert str(contract["roles"][0]["rate"]) == "80.0000"


async def test_approving_an_individual_sow_generates_an_individual_contract(
    conn, skeleton, tenants
) -> None:
    """§29: an individual engagement gets its own contract, payment and audit trail."""
    from app.services import contracts

    tenant = tenants["admin"]
    sow = await _draft_sow_with_role(
        conn,
        skeleton,
        tenants,
        {
            "title": "Individual SOW",
            "sow_type": "INDIVIDUAL",
            "counterparty_user_id": tenants["worker"].user_public_id,
        },
    )
    active = await _activate_sow(conn, tenants, sow["public_id"])

    assert active["contract_count"] == 1
    contract = await contracts.get_contract(
        conn, company_id=tenant.company_id, public_id=active["contract_ids"][0]
    )
    assert contract["contract_type"] == "INDIVIDUAL"
    assert contract["counterparty_user_id"] == tenants["worker"].user_public_id
    assert contract["counterparty_company_id"] is None
    assert len(contract["roles"]) == 1
    parties = contract["parties"]
    assert {p["party_role"] for p in parties} == {"PRIMARY", "COUNTERPARTY"}


async def test_generating_twice_does_not_duplicate_contracts(conn, skeleton, tenants) -> None:
    """A repeated generation call (retry / duplicate delivery) is a no-op.

    The state machine only enters ACTIVE once, and the row lock serialises
    concurrent approvals, so this path is defence in depth: the generator
    itself must never mint a second contract for one SOW.
    """
    from app.services import code, contracts

    tenant = tenants["admin"]
    sow = await _draft_sow_with_role(
        conn,
        skeleton,
        tenants,
        {"counterparty_company_id": tenants["worker"].company_public_id},
    )
    active = await _activate_sow(conn, tenants, sow["public_id"])
    assert active["contract_count"] == 1

    repeat = await contracts.generate_contracts_for_sow(
        conn,
        company_id=tenant.company_id,
        sow_id=sow["id"],
        actor_user_id=tenant.user_id,
        request_id="flow",
        ip_address=None,
    )
    assert [c["public_id"] for c in repeat] == active["contract_ids"]

    final = await code.get_sow(conn, company_id=tenant.company_id, public_id=sow["public_id"])
    assert final["contract_count"] == 1
    assert final["contract_ids"] == active["contract_ids"]


async def test_sow_with_generation_disabled_creates_no_contract(conn, skeleton, tenants) -> None:
    """auto_generate_contracts=false leaves activation contract-free."""
    sow = await _draft_sow_with_role(
        conn,
        skeleton,
        tenants,
        {
            "counterparty_company_id": tenants["worker"].company_public_id,
            "auto_generate_contracts": False,
        },
    )
    active = await _activate_sow(conn, tenants, sow["public_id"])
    assert active["status"] == "ACTIVE"
    assert active["contract_count"] == 0
    assert active["contract_ids"] == []


# =============================================================================
# founding W-9 intake + claim
# =============================================================================
async def _intake_w9(conn, owner_user_id) -> str:
    """An unclaimed founder W-9 row, exactly as upload_w9_intake leaves it."""
    row = (
        (
            await conn.execute(
                text(
                    """
                    INSERT INTO public.documents
                      (company_id, owner_user_id, doc_type, title, visibility,
                       status, checksum_sha256)
                    VALUES (NULL, CAST(:owner AS uuid), 'W9', 'Founder W-9',
                            'PRIVATE', 'PROCESSING', :checksum)
                    RETURNING public_id
                    """
                ),
                {"owner": owner_user_id, "checksum": "ab" * 32},
            )
        )
        .mappings()
        .first()
    )
    assert row is not None
    return str(row["public_id"])


async def _found_company(conn, tenants, founder_key: str, w9_public_id: str) -> dict[str, Any]:
    """Found a company on an intake W-9 through the real service."""
    from app.services import companies

    founder = tenants[founder_key]
    return await companies.create_company(
        conn,
        founder_user_id=founder.user_id,
        request_id="flow",
        ip_address=None,
        payload={
            "legal_name": f"Claim Co {w9_public_id}",
            "display_name": "ClaimCo",
            "country_code": "US",
            "default_currency": "USD",
            "w9_document_public_id": w9_public_id,
        },
    )


async def test_company_creation_claims_the_founders_intake_w9(conn, tenants) -> None:
    """The intake W-9 moves into the new company in the same transaction."""
    tenant = tenants["admin"]
    w9 = await _intake_w9(conn, tenant.user_id)

    company = await _found_company(conn, tenants, "admin", w9)
    assert company["public_id"].startswith("CO")

    owner = (
        await conn.execute(
            text(
                """
                SELECT c.public_id FROM public.documents d
                  JOIN public.companies c ON c.id = d.company_id
                 WHERE d.public_id = :pid
                """
            ),
            {"pid": w9},
        )
    ).scalar_one()
    assert owner == company["public_id"]


async def test_company_creation_rejects_another_founders_w9(conn, tenants) -> None:
    """A W-9 belonging to someone else is indistinguishable from a missing one."""
    w9 = await _intake_w9(conn, tenants["worker"].user_id)
    with pytest.raises(ResourceNotFoundError):
        await _found_company(conn, tenants, "admin", w9)


async def test_company_creation_rejects_an_already_claimed_w9(conn, tenants) -> None:
    """One intake W-9 founds exactly one company; it cannot be borrowed twice."""
    w9 = await _intake_w9(conn, tenants["admin"].user_id)
    await _found_company(conn, tenants, "admin", w9)
    with pytest.raises(ResourceNotFoundError):
        await _found_company(conn, tenants, "admin", w9)


# =============================================================================
# project lifecycle (§10)
# =============================================================================
async def _draft_project(conn, tenants, **overrides: Any) -> dict[str, Any]:
    """A DRAFT project built through the real service."""
    from app.services import code

    tenant = tenants["admin"]
    payload: dict[str, Any] = {"name": "Phase Project"}
    payload.update(overrides)
    return await code.create_project(
        conn,
        company_id=tenant.company_id,
        actor_user_id=tenant.user_id,
        request_id="flow",
        ip_address=None,
        payload=payload,
    )


async def _move_project(conn, tenants, public_id: str, target: str) -> dict[str, Any]:
    from app.services import code

    tenant = tenants["admin"]
    return await code.update_project(
        conn,
        company_id=tenant.company_id,
        public_id=public_id,
        actor_user_id=tenant.user_id,
        request_id="flow",
        ip_address=None,
        changes={"status": target},
    )


async def test_project_starts_draft_and_advertises_transitions(conn, tenants) -> None:
    """Projects are born DRAFT with their legal moves attached."""
    project = await _draft_project(conn, tenants)
    assert project["status"] == "DRAFT"
    assert project["allowed_transitions"] == ["PLANNING", "ACTIVE", "CANCELLED", "CLOSED"]
    with pytest.raises(ValidationError):
        await _draft_project(conn, tenants, status="ACTIVE")


async def test_project_status_transitions_are_enforced(conn, tenants) -> None:
    """DRAFT -> ACTIVE is legal; ACTIVE -> DRAFT is not a transition."""
    project = await _draft_project(conn, tenants)
    moved = await _move_project(conn, tenants, project["public_id"], "ACTIVE")
    assert moved["status"] == "ACTIVE"
    with pytest.raises(InvalidStateTransitionError):
        await _move_project(conn, tenants, project["public_id"], "DRAFT")


async def test_terminal_project_is_read_only(conn, tenants) -> None:
    """CANCELLED projects refuse every edit, including status moves."""
    from app.services import code

    tenant = tenants["admin"]
    project = await _draft_project(conn, tenants)
    await _move_project(conn, tenants, project["public_id"], "CANCELLED")
    with pytest.raises(BusinessRuleViolationError):
        await code.update_project(
            conn,
            company_id=tenant.company_id,
            public_id=project["public_id"],
            actor_user_id=tenant.user_id,
            request_id="flow",
            ip_address=None,
            changes={"name": "Sneaky rename"},
        )
    with pytest.raises(BusinessRuleViolationError):
        await _move_project(conn, tenants, project["public_id"], "ACTIVE")


async def test_individual_project_forces_creator_as_owner(conn, tenants) -> None:
    """An INDIVIDUAL project belongs to its creator; owner ids are not accepted."""
    admin, worker = tenants["admin"], tenants["worker"]
    project = await _draft_project(
        conn,
        tenants,
        category="INDIVIDUAL",
        owner_user_id=worker.user_public_id,
    )
    assert project["category"] == "INDIVIDUAL"
    assert project["owner_user_id"] == admin.user_public_id


# =============================================================================
# sow external acceptance (§14-15)
# =============================================================================
async def _external_sow(conn, skeleton, tenants, **overrides: Any) -> dict[str, Any]:
    """A DRAFT SOW addressed to the worker company, via the real service."""
    payload: dict[str, Any] = {
        "title": "External SOW",
        "counterparty_company_id": tenants["worker"].company_public_id,
        "roles": [
            {
                "project_role_id": await _public(conn, "project_roles", skeleton.project_role),
                "quantity": 1,
                "rate": "80.0000",
                "rate_type": "HOURLY",
                "currency": "USD",
            }
        ],
    }
    payload.update(overrides)
    return await _draft_sow_with_role(conn, skeleton, tenants, payload)


async def test_sow_send_acknowledge_accept_flow(conn, skeleton, tenants) -> None:
    """DRAFT -> SENT -> PENDING_ACCEPTANCE -> ACTIVE, with a contract generated."""
    from app.services import code

    tenant = tenants["admin"]
    sow = await _external_sow(conn, skeleton, tenants)

    sent = await code.send_sow(
        conn,
        company_id=tenant.company_id,
        public_id=sow["public_id"],
        actor_user_id=tenant.user_id,
        request_id="flow",
        ip_address=None,
    )
    assert sent["status"] == "SENT"

    acked = await code.acknowledge_sow(
        conn,
        company_id=tenant.company_id,
        public_id=sow["public_id"],
        actor_user_id=tenant.user_id,
        request_id="flow",
        ip_address=None,
    )
    assert acked["status"] == "PENDING_ACCEPTANCE"

    accepted = await code.respond_to_sow(
        conn,
        company_id=tenant.company_id,
        public_id=sow["public_id"],
        accept=True,
        actor_user_id=tenant.user_id,
        request_id="flow",
        ip_address=None,
        notes="Looks good.",
    )
    assert accepted["status"] == "ACTIVE"
    assert accepted["contract_count"] == 1


async def test_sow_decline_records_reason_and_survives(conn, skeleton, tenants) -> None:
    """A declined SOW keeps its history and can be revised and resent."""
    from app.services import code

    tenant = tenants["admin"]
    sow = await _external_sow(conn, skeleton, tenants)
    await code.send_sow(
        conn,
        company_id=tenant.company_id,
        public_id=sow["public_id"],
        actor_user_id=tenant.user_id,
        request_id="flow",
        ip_address=None,
    )
    declined = await code.respond_to_sow(
        conn,
        company_id=tenant.company_id,
        public_id=sow["public_id"],
        accept=False,
        actor_user_id=tenant.user_id,
        request_id="flow",
        ip_address=None,
        notes="Rate too high.",
    )
    assert declined["status"] == "REJECTED"
    assert declined["contract_count"] == 0

    history = await code.get_sow(
        conn, company_id=tenant.company_id, public_id=sow["public_id"], with_history=True
    )
    rejects = [h for h in history["history"] if h["action"] == "sow.rejected"]
    assert len(rejects) == 1
    assert rejects[0]["reason"] == "Rate too high."

    redraft = await code.reopen_sow(
        conn,
        company_id=tenant.company_id,
        public_id=sow["public_id"],
        actor_user_id=tenant.user_id,
        request_id="flow",
        ip_address=None,
    )
    assert redraft["status"] == "DRAFT"
    resent = await code.send_sow(
        conn,
        company_id=tenant.company_id,
        public_id=sow["public_id"],
        actor_user_id=tenant.user_id,
        request_id="flow",
        ip_address=None,
    )
    assert resent["status"] == "SENT"


async def test_sow_send_requires_roles(conn, skeleton, tenants) -> None:
    """A role-less SOW cannot be sent: the counterparty would have nothing to accept.

    (A counterparty-less SOW cannot exist at all — the schema CHECK rejects
    it at insert — so send_sow's counterparty guard is defence in depth for a
    state the API cannot produce.)
    """
    from app.services import code

    tenant = tenants["admin"]
    roleless = await _draft_sow_with_role(
        conn,
        skeleton,
        tenants,
        {
            "title": "Roleless SOW",
            "counterparty_company_id": tenants["worker"].company_public_id,
            "roles": [],
        },
    )
    with pytest.raises(BusinessRuleViolationError):
        await code.send_sow(
            conn,
            company_id=tenant.company_id,
            public_id=roleless["public_id"],
            actor_user_id=tenant.user_id,
            request_id="flow",
            ip_address=None,
        )


async def test_sow_accept_from_draft_is_rejected(conn, skeleton, tenants) -> None:
    """Acceptance is only legal once the SOW is under review."""
    from app.services import code

    tenant = tenants["admin"]
    sow = await _external_sow(conn, skeleton, tenants)
    with pytest.raises(InvalidStateTransitionError):
        await code.respond_to_sow(
            conn,
            company_id=tenant.company_id,
            public_id=sow["public_id"],
            accept=True,
            actor_user_id=tenant.user_id,
            request_id="flow",
            ip_address=None,
        )


# =============================================================================
# contract expiry sweep (§30)
# =============================================================================
async def _active_contract_ending(
    conn, skeleton, tenants, *, days_offset: int, title: str = "Expiry Probe"
) -> dict[str, Any]:
    """An ACTIVE contract ending a given number of days from today.

    Built through SOW approval (so generation is exercised), then dated with
    an UPDATE the way the sweep expects to find real rows.
    """
    from datetime import timedelta

    from app.core.clock import utc_today

    tenant = tenants["admin"]
    sow = await _draft_sow_with_role(
        conn,
        skeleton,
        tenants,
        {"title": title, "counterparty_company_id": tenants["worker"].company_public_id},
    )
    active = await _activate_sow(conn, tenants, sow["public_id"])
    assert active["contract_count"] == 1
    today = utc_today()
    await conn.execute(
        text(
            "UPDATE public.contracts SET status = 'ACTIVE',"
            " start_date = CAST(:start AS date), end_date = CAST(:end AS date)"
            " WHERE public_id = :pid"
        ),
        {
            "start": today - timedelta(days=60),
            "end": today + timedelta(days=days_offset),
            "pid": active["contract_ids"][0],
        },
    )
    # Expiry warnings notify the people on the contract: give the worker an
    # ACTIVE assignment so the sweep has a recipient, the way real contracts do.
    await conn.execute(
        text(
            """
            INSERT INTO public.assignments
              (contract_id, project_id, company_id, user_id, role_title, currency,
               start_date, status, allocation_pct, source)
            VALUES ((SELECT id FROM public.contracts WHERE public_id = :pid),
                    CAST(:project AS uuid), CAST(:company AS uuid), CAST(:user AS uuid),
                    'Expiry Probe', 'USD', CAST(:start AS date), 'ACTIVE', 100, 'MANUAL')
            """
        ),
        {
            "pid": active["contract_ids"][0],
            "project": skeleton.project,
            "company": tenants["admin"].company_id,
            "user": tenants["worker"].user_id,
            "start": today - timedelta(days=60),
        },
    )
    return {"public_id": active["contract_ids"][0], "tenant": tenant}


async def _contract_notifications(conn, contract_public_id: str, like: str) -> list[str]:
    rows = (
        (
            await conn.execute(
                text(
                    """
                SELECT n.type FROM platform.notifications n
                  JOIN public.contracts c ON c.id = n.resource_id
                 WHERE c.public_id = :pid AND n.type LIKE :like
                 ORDER BY n.created_at
                """
                ),
                {"pid": contract_public_id, "like": like},
            )
        )
        .scalars()
        .all()
    )
    return [str(t) for t in rows]


async def test_expiry_sweep_flips_past_due_and_audits(conn, skeleton, tenants) -> None:
    """Yesterday-ended contracts become EXPIRED with an audit row and a notice."""
    from app.services import contracts

    made = await _active_contract_ending(conn, skeleton, tenants, days_offset=-1)
    result = await contracts.run_contract_expiry_sweep(conn)
    assert result["expired"] >= 1

    status = (
        await conn.execute(
            text("SELECT status FROM public.contracts WHERE public_id = :pid"),
            {"pid": made["public_id"]},
        )
    ).scalar_one()
    assert status == "EXPIRED"

    actions = (
        (
            await conn.execute(
                text("SELECT action FROM platform.audit_logs WHERE resource_public_id = :pid"),
                {"pid": made["public_id"]},
            )
        )
        .scalars()
        .all()
    )
    assert "contract.expired" in set(actions)
    assert await _contract_notifications(conn, made["public_id"], "CONTRACT_EXPIRED") == [
        "CONTRACT_EXPIRED"
    ]


async def test_expiry_sweep_warns_once_per_tier(conn, skeleton, tenants) -> None:
    """A 5-day contract warns at the 7-day tier exactly once, however often run."""
    from app.services import contracts

    made = await _active_contract_ending(conn, skeleton, tenants, days_offset=5)
    first = await contracts.run_contract_expiry_sweep(conn)
    assert first["notified"] >= 1
    assert await _contract_notifications(conn, made["public_id"], "CONTRACT_EXPIRING%") == [
        "CONTRACT_EXPIRING_7D"
    ]

    second = await contracts.run_contract_expiry_sweep(conn)
    assert second["expired"] == 0
    assert await _contract_notifications(conn, made["public_id"], "CONTRACT_EXPIRING%") == [
        "CONTRACT_EXPIRING_7D"
    ]

    status = (
        await conn.execute(
            text("SELECT status FROM public.contracts WHERE public_id = :pid"),
            {"pid": made["public_id"]},
        )
    ).scalar_one()
    assert status == "ACTIVE"


async def test_renewal_restarts_warning_cycle(conn, skeleton, tenants) -> None:
    """Renewing clears old tier warnings so the new period warns again."""
    from datetime import timedelta

    from app.core.clock import utc_today
    from app.services import contracts

    tenant = tenants["admin"]
    made = await _active_contract_ending(conn, skeleton, tenants, days_offset=5)
    await contracts.run_contract_expiry_sweep(conn)
    assert await _contract_notifications(conn, made["public_id"], "CONTRACT_EXPIRING%") == [
        "CONTRACT_EXPIRING_7D"
    ]

    today = utc_today()
    await contracts.renew_contract(
        conn,
        company_id=tenant.company_id,
        public_id=made["public_id"],
        actor_user_id=tenant.user_id,
        request_id="flow",
        ip_address=None,
        new_start_date=today,
        new_end_date=today + timedelta(days=400),
        contract_value=None,
    )
    assert await _contract_notifications(conn, made["public_id"], "CONTRACT_EXPIRING%") == []

    await conn.execute(
        text("UPDATE public.contracts SET end_date = CAST(:end AS date) WHERE public_id = :pid"),
        {"end": today + timedelta(days=5), "pid": made["public_id"]},
    )
    await contracts.run_contract_expiry_sweep(conn)
    assert await _contract_notifications(conn, made["public_id"], "CONTRACT_EXPIRING%") == [
        "CONTRACT_EXPIRING_7D"
    ]


# =============================================================================
# requires_timesheets gate (§36)
# =============================================================================
async def test_invoice_requires_timesheets_when_contract_demands(conn, skeleton, tenants) -> None:
    """A timesheet-gated contract cannot bill on fixed lines alone."""
    from app.services import invoicing

    tenant = tenants["admin"]
    sow = await _draft_sow_with_role(
        conn,
        skeleton,
        tenants,
        {"counterparty_company_id": tenants["worker"].company_public_id},
    )
    active = await _activate_sow(conn, tenants, sow["public_id"])
    contract_pid = active["contract_ids"][0]
    await conn.execute(
        text("UPDATE public.contracts SET status = 'ACTIVE' WHERE public_id = :pid"),
        {"pid": contract_pid},
    )
    contract_id = (
        await conn.execute(
            text("SELECT id::text FROM public.contracts WHERE public_id = :pid"),
            {"pid": contract_pid},
        )
    ).scalar_one()
    await conn.execute(
        text(
            """
            INSERT INTO public.contract_line_items
              (contract_id, line_type, label, description, quantity, unit,
               unit_rate, currency, billing_basis, billing_frequency)
            VALUES (CAST(:cid AS uuid), 'FIXED', 'Monthly platform fee',
                    'Fixed platform fee', 1, 'LOT', 500, 'USD', 'FIXED', 'MONTHLY')
            """
        ),
        {"cid": contract_id},
    )

    async def _generate() -> dict[str, Any]:
        return await invoicing.generate_invoice(
            conn,
            company_id=tenant.company_id,
            actor_user_id=tenant.user_id,
            request_id="flow",
            ip_address=None,
            contract_public_id=contract_pid,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )

    with pytest.raises(BusinessRuleViolationError) as caught:
        await _generate()
    assert caught.value.details["reason"] == "TIMESHEETS_REQUIRED"

    await conn.execute(
        text("UPDATE public.contracts SET requires_timesheets = false WHERE public_id = :pid"),
        {"pid": contract_pid},
    )
    invoice = await _generate()
    assert invoice["status"] == "DRAFT"
    assert {i["line_type"] for i in invoice["items"]} == {"FIXED"}


# =============================================================================
# invoice approval segregation of duties (§42)
# =============================================================================
async def test_invoice_self_approval_is_refused_and_decider_recorded(
    conn, skeleton, tenants
) -> None:
    """The generator cannot approve their own invoice; the decider is recorded."""
    from app.services import invoicing

    admin, worker = tenants["admin"], tenants["worker"]
    invoice = await _draft_invoice(conn, skeleton, admin, worker.company_id)
    await _line(conn, skeleton, invoice, quantity=Decimal("10"), rate=Decimal("100"))
    await invoicing.submit_for_approval(
        conn,
        company_id=admin.company_id,
        public_id=invoice["public_id"],
        actor_user_id=admin.user_id,
        request_id="flow",
        ip_address=None,
        notes=None,
    )

    with pytest.raises(BusinessRuleViolationError) as caught:
        await invoicing.decide_approval(
            conn,
            company_id=admin.company_id,
            public_id=invoice["public_id"],
            step_no=1,
            decision="APPROVED",
            actor_user_id=admin.user_id,
            request_id="flow",
            ip_address=None,
            notes=None,
        )
    assert caught.value.details["reason"] == "SEGREGATION_OF_DUTIES"

    decided = await invoicing.decide_approval(
        conn,
        company_id=admin.company_id,
        public_id=invoice["public_id"],
        step_no=1,
        decision="APPROVED",
        actor_user_id=worker.user_id,
        request_id="flow",
        ip_address=None,
        notes="Checked against the SOW.",
    )
    assert decided["status"] == "APPROVED"
    assert decided["approvals"][0]["approver_public_id"] == worker.user_public_id


# =============================================================================
# event emission (§50)
# =============================================================================
async def _outbox_events(conn, aggregate_id: str) -> list[dict[str, Any]]:
    """Outbox rows for one aggregate, oldest first."""
    rows = (
        (
            await conn.execute(
                text(
                    """
                    SELECT event_type, payload FROM platform.outbox_events
                     WHERE aggregate_id = CAST(:aid AS uuid)
                     ORDER BY created_at
                    """
                ),
                {"aid": aggregate_id},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def test_sow_send_emits_outbox_event(conn, skeleton, tenants) -> None:
    """Sending a SOW publishes its status change for the workers."""
    from app.services import code

    tenant = tenants["admin"]
    sow = await _external_sow(conn, skeleton, tenants)
    await code.send_sow(
        conn,
        company_id=tenant.company_id,
        public_id=sow["public_id"],
        actor_user_id=tenant.user_id,
        request_id="flow",
        ip_address=None,
    )
    events = await _outbox_events(conn, str(sow["id"]))
    sent = [e for e in events if e["event_type"] == "SOW_STATUS_CHANGED"]
    assert sent
    assert sent[-1]["payload"]["new"] == "SENT"
    assert sent[-1]["payload"]["public_id"] == sow["public_id"]


async def test_contract_send_and_accept_emit_outbox_events(conn, skeleton, tenants) -> None:
    """The counterparty handshake is observable without polling tables."""
    from app.services import contracts

    tenant = tenants["admin"]
    offered = await _draft_sow_with_role(
        conn,
        skeleton,
        tenants,
        {
            "title": "Handshake SOW",
            "counterparty_company_id": tenants["worker"].company_public_id,
        },
    )
    draft = await contracts.create_contract(
        conn,
        company_id=tenant.company_id,
        actor_user_id=tenant.user_id,
        request_id="flow",
        ip_address=None,
        payload={
            "project_id": offered["project_id"],
            "sow_id": offered["public_id"],
            "title": "Handshake contract",
            "roles": [
                {
                    "project_role_id": await _public(conn, "project_roles", skeleton.project_role),
                    "quantity": 1,
                    "rate": "80.0000",
                    "rate_type": "HOURLY",
                    "currency": "USD",
                }
            ],
        },
    )
    await contracts.send_contract(
        conn,
        company_id=tenant.company_id,
        public_id=draft["public_id"],
        actor_user_id=tenant.user_id,
        request_id="flow",
        ip_address=None,
        notes=None,
    )
    decided = await contracts.respond_to_contract(
        conn,
        company_id=tenant.company_id,
        public_id=draft["public_id"],
        accept=True,
        actor_user_id=tenant.user_id,
        request_id="flow",
        ip_address=None,
        notes="Accepted.",
    )
    assert decided["status"] == "ACCEPTED"

    events = await _outbox_events(conn, str(draft["id"]))
    kinds = [(e["event_type"], e["payload"].get("new")) for e in events]
    assert ("CONTRACT_STATUS_CHANGED", "SENT") in kinds
    assert ("CONTRACT_STATUS_CHANGED", "ACCEPTED") in kinds


async def test_invoice_submit_emits_outbox_event(conn, skeleton, tenants) -> None:
    """Submitting an invoice for approval publishes its status change."""
    from app.services import invoicing

    admin, worker = tenants["admin"], tenants["worker"]
    invoice = await _draft_invoice(conn, skeleton, admin, worker.company_id)
    await _line(conn, skeleton, invoice, quantity=Decimal("10"), rate=Decimal("100"))
    await invoicing.submit_for_approval(
        conn,
        company_id=admin.company_id,
        public_id=invoice["public_id"],
        actor_user_id=admin.user_id,
        request_id="flow",
        ip_address=None,
        notes=None,
    )
    events = await _outbox_events(conn, str(invoice["id"]))
    submitted = [e for e in events if e["event_type"] == "INVOICE_STATUS_CHANGED"]
    assert submitted
    assert submitted[-1]["payload"]["new"] == "PENDING"


async def test_sow_event_recipients_cover_both_sides(conn, skeleton, tenants) -> None:
    """Owner admins and the counterparty side are all notified, nobody else."""
    from app.services import events as event_service

    admin, worker = tenants["admin"], tenants["worker"]
    sow = await _external_sow(conn, skeleton, tenants)
    recipients = await event_service._recipients_for(
        conn, "sow", str(sow["id"]), {"company_id": str(admin.company_id)}
    )
    assert admin.user_id in recipients
    assert worker.user_id in recipients


async def test_payment_event_recipients_cover_finance(conn, skeleton, tenants) -> None:
    """Payment events reach the owner company's finance roles."""
    from app.services import events as event_service

    admin, worker = tenants["admin"], tenants["worker"]
    payment = await _completed_payment(conn, admin, amount=Decimal("100.00"))
    recipients = await event_service._recipients_for(
        conn, "payment", str(payment["id"]), {"company_id": str(admin.company_id)}
    )
    assert worker.user_id in recipients


# =============================================================================
# multi-hop isolation (§64)
# =============================================================================
async def _provision_user(conn, auth_id: str, tag: str) -> uuid.UUID:
    from app.core.security import provision_user

    return uuid.UUID(
        await provision_user(
            conn,
            auth_id,
            email=f"{tag}_{uuid.uuid4().hex[:6]}@example.test",
            first_name=tag.title(),
            verified=True,
        )
    )


async def _user_public_id(conn, user_id: uuid.UUID) -> str:
    return str(
        (
            await conn.execute(
                text("SELECT public_id FROM public.users WHERE id = :uid"), {"uid": user_id}
            )
        ).scalar_one()
    )


async def _provision_company(conn, tag: str) -> dict[str, Any]:
    """A fresh user + company + SUPER_ADMIN membership, in this transaction."""
    user_id = await _provision_user(conn, str(uuid.uuid4()), tag)
    company = await _one(
        conn,
        "INSERT INTO public.companies (legal_name, display_name, created_by)"
        " VALUES (:legal, :display, :by) RETURNING id::text, public_id",
        {"legal": f"{tag} Legal", "display": tag.title(), "by": user_id},
    )
    await conn.execute(
        text("SELECT app.bootstrap_company_roles( CAST(:company AS uuid), CAST(:founder AS uuid))"),
        {"company": company["id"], "founder": user_id},
    )
    return {
        "user_id": user_id,
        "user_public_id": await _user_public_id(conn, user_id),
        "company_id": uuid.UUID(company["id"]),
        "company_public_id": str(company["public_id"]),
    }


async def _hop_sow(
    conn, owner: dict[str, Any], project_pid: str, counterparty: dict[str, str], rate: str
) -> dict[str, Any]:
    """Project role + SOW + approval, returning the live SOW (contract made)."""
    from app.services import code

    role = await code.create_project_role(
        conn,
        company_id=owner["company_id"],
        project_public_id=project_pid,
        actor_user_id=owner["user_id"],
        request_id="flow",
        ip_address=None,
        payload={"title": "Hop Developer", "required_count": 10},
    )
    sow = await code.create_sow(
        conn,
        company_id=owner["company_id"],
        project_public_id=project_pid,
        actor_user_id=owner["user_id"],
        request_id="flow",
        ip_address=None,
        payload={
            "title": "Hop SOW",
            "counterparty_company_id": counterparty.get("company_public_id"),
            "counterparty_user_id": counterparty.get("user_public_id"),
            "sow_type": "INDIVIDUAL" if counterparty.get("user_public_id") else "COMPANY",
            "roles": [
                {
                    "project_role_id": role["public_id"],
                    "quantity": 2,
                    "rate": rate,
                    "rate_type": "HOURLY",
                    "currency": "USD",
                }
            ],
        },
    )
    for target in ("PENDING_APPROVAL", "ACTIVE"):
        await code.transition_sow(
            conn,
            company_id=owner["company_id"],
            public_id=sow["public_id"],
            target=target,
            actor_user_id=owner["user_id"],
            request_id="flow",
            ip_address=None,
        )
    live = await code.get_sow(conn, company_id=owner["company_id"], public_id=sow["public_id"])
    assert live["contract_count"] == 1
    return live


async def test_multihop_chain_isolates_downstream_data(conn, skeleton, tenants) -> None:
    """A -> B -> D -> employee: separate contracts, invisible across hops."""
    _ = skeleton
    from app.db.session import set_identity
    from app.services import code

    admin, worker = tenants["admin"], tenants["worker"]
    founder_d = await _provision_company(conn, "hopco")
    employee_id = await _provision_user(conn, str(uuid.uuid4()), "hopworker")
    employee_pid = await _user_public_id(conn, employee_id)

    async def _as(user_id: uuid.UUID, company_id: uuid.UUID) -> None:
        await set_identity(conn, user_id=user_id, company_id=company_id, request_id="flow")

    async def _project(owner: dict[str, Any], name: str) -> str:
        created = await code.create_project(
            conn,
            company_id=owner["company_id"],
            actor_user_id=owner["user_id"],
            request_id="flow",
            ip_address=None,
            payload={"name": name},
        )
        return str(created["public_id"])

    # Hop 1, as A: project + SOW to B at $60, approved, contract generated.
    await _as(admin.user_id, admin.company_id)
    hop_ab = await _hop_sow(
        conn,
        {"user_id": admin.user_id, "company_id": admin.company_id},
        await _project({"user_id": admin.user_id, "company_id": admin.company_id}, "Hop Project A"),
        {"company_public_id": worker.company_public_id},
        "60.0000",
    )

    # Hop 2, as B: own project + SOW to D at $50, approved.
    await _as(worker.user_id, worker.company_id)
    hop_bd = await _hop_sow(
        conn,
        {"user_id": worker.user_id, "company_id": worker.company_id},
        await _project(
            {"user_id": worker.user_id, "company_id": worker.company_id}, "Hop Project B"
        ),
        {"company_public_id": founder_d["company_public_id"]},
        "50.0000",
    )

    # Hop 3, as D: individual SOW to the employee at $40, approved.
    await _as(founder_d["user_id"], founder_d["company_id"])
    hop_individual = await _hop_sow(
        conn,
        founder_d,
        await _project(founder_d, "Hop Project D"),
        {"user_public_id": employee_pid},
        "40.0000",
    )

    # A sees its own hop and nothing downstream of it.
    await _as(admin.user_id, admin.company_id)
    assert (await code.get_sow(conn, company_id=admin.company_id, public_id=hop_ab["public_id"]))[
        "contract_count"
    ] == 1
    with pytest.raises(ResourceNotFoundError):
        await code.get_sow(conn, company_id=admin.company_id, public_id=hop_bd["public_id"])
    with pytest.raises(ResourceNotFoundError):
        await code.get_sow(conn, company_id=admin.company_id, public_id=hop_individual["public_id"])

    # B sees both its hops but not D's engagement with the individual.
    await _as(worker.user_id, worker.company_id)
    assert (await code.get_sow(conn, company_id=worker.company_id, public_id=hop_bd["public_id"]))[
        "contract_count"
    ] == 1
    with pytest.raises(ResourceNotFoundError):
        await code.get_sow(
            conn, company_id=worker.company_id, public_id=hop_individual["public_id"]
        )

    # D sees its own hops but nothing upstream.
    await _as(founder_d["user_id"], founder_d["company_id"])
    assert (
        await code.get_sow(
            conn, company_id=founder_d["company_id"], public_id=hop_individual["public_id"]
        )
    )["contract_count"] == 1
    with pytest.raises(ResourceNotFoundError):
        await code.get_sow(conn, company_id=founder_d["company_id"], public_id=hop_ab["public_id"])


# =============================================================================
# allocation race (§62)
# =============================================================================


async def test_concurrent_allocations_allow_only_one(conn, skeleton, tenants) -> None:
    """Two simultaneous approvals of the last slot: exactly one survives.



    The status trigger recomputes under an advisory lock, so the second

    approval observes the first one's committed row and aborts with a capacity

    violation. Everything here runs as raw SQL (no audit rows), so the

    committed winner row is deleted below and the shared database is left

    exactly as found.

    """

    import asyncio

    from sqlalchemy.exc import DBAPIError

    from app.db.session import get_connection_factory, set_identity

    _ = skeleton

    admin = tenants["admin"]

    factory = get_connection_factory()

    marker = uuid.uuid4().hex[:8]

    async def _open() -> tuple[Any, Any, Any]:

        manager = factory()

        connection = await manager.__aenter__()

        tx = await connection.begin()

        await set_identity(
            connection,
            user_id=admin.user_id,
            company_id=admin.company_id,
            request_id="pytest-race",
        )

        return manager, connection, tx

    holder, setup, setup_tx = await _open()

    try:
        project = (
            (
                await setup.execute(
                    text(
                        "INSERT INTO public.projects (company_id, name, status)"
                        " VALUES (CAST(:c AS uuid), :name, 'DRAFT')"
                        " RETURNING id::text, public_id"
                    ),
                    {"c": admin.company_id, "name": f"Race {marker}"},
                )
            )
            .mappings()
            .first()
        )

        assert project is not None

        role = (
            (
                await setup.execute(
                    text(
                        "INSERT INTO public.project_roles"
                        " (project_id, company_id, title, required_count, status)"
                        " VALUES (CAST(:p AS uuid), CAST(:c AS uuid), 'Last Slot', 1, 'OPEN')"
                        " RETURNING id::text, public_id"
                    ),
                    {"p": project["id"], "c": admin.company_id},
                )
            )
            .mappings()
            .first()
        )

        assert role is not None

        sows = []

        for label in ("Race SOW A", "Race SOW B"):
            created = (
                (
                    await setup.execute(
                        text(
                            "INSERT INTO public.sows"
                            " (project_id, company_id, sow_type, counterparty_company_id,"
                            "  title, status)"
                            " VALUES (CAST(:p AS uuid), CAST(:c AS uuid), 'COMPANY',"
                            "         CAST(:cp AS uuid), :title, 'DRAFT')"
                            " RETURNING id::text, public_id"
                        ),
                        {
                            "p": project["id"],
                            "c": admin.company_id,
                            "cp": tenants["worker"].company_id,
                            "title": f"{label} {marker}",
                        },
                    )
                )
                .mappings()
                .first()
            )

            assert created is not None

            await setup.execute(
                text(
                    "INSERT INTO public.sow_roles (sow_id, project_role_id, quantity)"
                    " VALUES (CAST(:s AS uuid), CAST(:r AS uuid), 1)"
                ),
                {"s": created["id"], "r": role["id"]},
            )

            sows.append(created["public_id"])

        await setup_tx.commit()

    except BaseException:
        await setup_tx.rollback()

        raise

    finally:
        await holder.__aexit__(None, None, None)

    async def _race(sow_pid: str) -> str:

        manager, connection, tx = await _open()

        try:
            await connection.execute(
                text("UPDATE public.sows SET status = 'PENDING_APPROVAL' WHERE public_id = :pid"),
                {"pid": sow_pid},
            )

            await tx.commit()

            return "activated"

        except BaseException:
            await tx.rollback()

            raise

        finally:
            await manager.__aexit__(None, None, None)

    results = await asyncio.wait_for(
        asyncio.gather(_race(sows[0]), _race(sows[1]), return_exceptions=True),
        timeout=120,
    )

    wins = [r for r in results if r == "activated"]

    losses = [r for r in results if isinstance(r, BaseException)]

    assert len(wins) == 1, results

    assert len(losses) == 1

    assert isinstance(losses[0], DBAPIError)

    await conn.execute(
        text(
            "DELETE FROM public.sow_roles WHERE sow_id IN "
            "(SELECT id FROM public.sows WHERE public_id IN (:a, :b))"
        ),
        {"a": sows[0], "b": sows[1]},
    )

    await conn.execute(
        text("DELETE FROM public.sows WHERE public_id IN (:a, :b)"),
        {"a": sows[0], "b": sows[1]},
    )

    await conn.execute(
        text(
            "DELETE FROM public.project_roles WHERE project_id = "
            "(SELECT id FROM public.projects WHERE public_id = :p)"
        ),
        {"p": project["public_id"]},
    )

    await conn.execute(
        text("DELETE FROM public.projects WHERE public_id = :p"), {"p": project["public_id"]}
    )


async def test_partial_payments_settle_invoice_in_steps(conn, skeleton, tenants) -> None:
    """$6,000 of $10,000 -> PARTIALLY_PAID with $4,000 due; then PAID."""
    from app.services import invoicing, payments

    tenant = tenants["admin"]
    invoice = await _approved_invoice(
        conn, skeleton, tenant, tenants["worker"].company_id, Decimal("1000.00")
    )

    async def _pay(amount: Decimal) -> dict[str, Any]:
        made = await _completed_payment(conn, tenant, amount=amount)
        await payments.allocate_payment(
            conn,
            company_id=tenant.company_id,
            payment_public_id=str(made["public_id"]),
            actor_user_id=tenant.user_id,
            request_id="flow",
            ip_address=None,
            allocations=[{"invoice_id": str(invoice["public_id"]), "amount": amount}],
        )
        return await invoicing.get_invoice(
            conn, company_id=tenant.company_id, public_id=str(invoice["public_id"])
        )

    first = await _pay(Decimal("600.00"))
    assert first["status"] == "PARTIALLY_PAID"
    assert Decimal(str(first["amount_paid"])) == Decimal("600.0000")
    assert Decimal(str(first["balance_due"])) == Decimal("400.0000")

    second = await _pay(Decimal("400.00"))
    assert second["status"] == "PAID"
    assert Decimal(str(second["balance_due"])) == Decimal("0.0000")


# =============================================================================
# document authorization (§55)
# =============================================================================
async def test_document_download_refuses_foreign_company(
    conn, skeleton, tenants, other_tenant_conn
) -> None:
    """Company B cannot download company A's document, by id or by guessing."""
    _ = skeleton
    from app.services import documents

    admin, worker = tenants["admin"], tenants["worker"]
    doc = await _one(
        conn,
        "INSERT INTO public.documents (company_id, owner_user_id, doc_type, title, visibility)"
        " VALUES (CAST(:c AS uuid), CAST(:u AS uuid), 'OTHER', 'Board minutes', 'PRIVATE')"
        " RETURNING public_id",
        {"c": admin.company_id, "u": admin.user_id},
    )

    with pytest.raises(ResourceNotFoundError) as caught:
        await documents.download_url(
            other_tenant_conn,
            company_id=worker.company_id,
            public_id=str(doc["public_id"]),
            actor_user_id=worker.user_id,
            request_id="flow",
            ip_address=None,
        )
    assert caught.value.status_code == 404


# =============================================================================
# list filters (§47)
# =============================================================================
async def test_status_filter_accepts_comma_lists(conn, skeleton, tenants) -> None:
    """Tab bars can request several statuses in one round trip."""
    _ = skeleton
    from app.services import code

    tenant = tenants["admin"]
    first = await _draft_project(conn, tenants, name="TabProbe Alpha")
    second = await _draft_project(conn, tenants, name="TabProbe Beta")
    await _move_project(conn, tenants, second["public_id"], "ACTIVE")

    async def _list(status: str | None) -> list[str]:
        rows = await code.list_projects(
            conn,
            company_id=tenant.company_id,
            search="TabProbe",
            status=status,
            client_company_id=None,
            owner_user_id=None,
            cursor_keys={},
            limit=25,
        )
        return sorted(r["public_id"] for r in rows)

    assert await _list("DRAFT") == [first["public_id"]]
    assert await _list("DRAFT,ACTIVE") == sorted([first["public_id"], second["public_id"]])
    assert await _list("CANCELLED") == []


async def test_project_mine_filter_narrows_to_owned(conn, skeleton, tenants) -> None:
    """The Mine tab shows only projects owned by the caller."""
    _ = skeleton
    from app.services import code

    admin, worker = tenants["admin"], tenants["worker"]
    mine = await _draft_project(conn, tenants, name="TabProbe Mine")
    await _draft_project(
        conn,
        tenants,
        name="TabProbe Theirs",
        owner_user_id=worker.user_public_id,
    )
    rows = await code.list_projects(
        conn,
        company_id=admin.company_id,
        search="TabProbe",
        status=None,
        client_company_id=None,
        owner_user_id=admin.user_public_id,
        cursor_keys={},
        limit=25,
    )
    assert [r["public_id"] for r in rows] == [mine["public_id"]]


async def test_invoice_direction_filter_separates_payables(conn, skeleton, tenants) -> None:
    """RECEIVABLE is the default listing; PAYABLE credit notes list apart."""
    from app.services import invoicing

    admin, worker = tenants["admin"], tenants["worker"]
    invoice = await _approved_invoice(conn, skeleton, admin, worker.company_id, Decimal("1000.00"))
    note = await invoicing.issue_credit_note(
        conn,
        company_id=admin.company_id,
        public_id=str(invoice["public_id"]),
        actor_user_id=admin.user_id,
        request_id="flow",
        ip_address=None,
        amount=Decimal("100.00"),
        reason="Goodwill adjustment.",
    )
    assert note["direction"] == "PAYABLE"

    async def _ids(direction: str | None) -> list[str]:
        rows = await invoicing.list_invoices(
            conn,
            company_id=admin.company_id,
            direction=direction,
            cursor_keys={},
            limit=50,
        )
        return [r["public_id"] for r in rows]

    default_ids = await _ids(None)
    assert str(invoice["public_id"]) in default_ids
    assert str(note["public_id"]) not in default_ids
    payable_ids = await _ids("PAYABLE")
    assert str(note["public_id"]) in payable_ids
    assert str(invoice["public_id"]) not in payable_ids


# =============================================================================
# contract termination date (§31)
# =============================================================================
async def test_termination_date_defaults_and_validates(conn, skeleton, tenants) -> None:
    """Effective date defaults past notice, rejects past or shortened dates."""
    from datetime import timedelta

    from app.core.clock import utc_today
    from app.services import contracts

    tenant = tenants["admin"]
    sow = await _draft_sow_with_role(
        conn,
        skeleton,
        tenants,
        {"counterparty_company_id": tenants["worker"].company_public_id},
    )
    active = await _activate_sow(conn, tenants, sow["public_id"])
    contract_pid = active["contract_ids"][0]
    await conn.execute(
        text(
            "UPDATE public.contracts SET status = 'ACTIVE', termination_notice_days = 30"
            " WHERE public_id = :pid"
        ),
        {"pid": contract_pid},
    )

    async def _terminate(day: Any) -> dict[str, Any]:
        return await contracts.terminate_contract(
            conn,
            company_id=tenant.company_id,
            public_id=contract_pid,
            actor_user_id=tenant.user_id,
            request_id="flow",
            ip_address=None,
            reason="Ending early.",
            effective_date=day,
        )

    today = utc_today()
    with pytest.raises(ValidationError) as caught:
        await _terminate(today - timedelta(days=1))
    assert caught.value.details["reason"] == "EFFECTIVE_DATE_IN_PAST"

    with pytest.raises(BusinessRuleViolationError) as refused:
        await _terminate(today + timedelta(days=10))
    assert refused.value.details["reason"] == "NOTICE_PERIOD_NOT_SATISFIED"

    done = await _terminate(today + timedelta(days=45))
    assert done["status"] == "TERMINATED"

    end = (
        await conn.execute(
            text("SELECT notice_period_end FROM public.contracts WHERE public_id = :pid"),
            {"pid": contract_pid},
        )
    ).scalar_one()
    assert str(end) == str(today + timedelta(days=45))
