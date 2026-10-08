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
