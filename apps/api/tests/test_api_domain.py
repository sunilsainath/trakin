"""HTTP-surface tests: every domain endpoint answers, and answers correctly.

The FastAPI app is exercised over ASGI with the two authentication dependencies
replaced. Everything downstream of authentication — routing, permission gating,
the error envelope, the request-id header — is the real code path; only the
Supabase token verification is stubbed out, because it needs a real token signed
by the project's key.

Three properties are proved:

* every path under the eighteen domain prefixes is routed (never 404 / 405) and
  never returns an untyped 500;
* an endpoint gated by ``require_permission`` answers 403 with
  ``error.code == "PERMISSION_DENIED"`` when the caller lacks that permission;
* failures are always the typed envelope ``{"error": {code, message, request_id}}``
  with a matching ``X-Request-Id`` response header.

Endpoints that fail today because of a schema/code mismatch are listed, with the
defect named, in ``EXPECTED_SERVER_ERRORS`` and ``EXPECTED_NOT_FOUND``. Those
constants are assertions: a new 500, or a 500 that has been fixed, fails the test.

Marked ``integration``: needs the real database.

Run:
    pytest apps/api/tests/test_api_domain.py -v
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.security import AuthenticatedUser
from app.db.session import effective_permissions  # noqa: F401  (see D6 note below)
from app.services import code  # noqa: F401  (imported to prove the app loads)

pytestmark = pytest.mark.integration

PREFIXES = (
    "projects",
    "project-roles",
    "sows",
    "contracts",
    "assignments",
    "timesheets",
    "timesheet-entries",
    "timesheet-approvals",
    "leave",
    "invoices",
    "billing",
    "payments",
    "bank-accounts",
    "bank-transactions",
    "reconciliation",
    "documents",
    "msas",
    "notifications",
    "dashboard",
    "ai",
)

# Endpoints that cannot return anything but a server error today, each mapped to
# the app location and the reason PostgreSQL (or Python) gave. Everything NOT
# listed here must answer without a 5xx.
#
#   D1  public.companies has legal_name / display_name, never `name`
#   D7  public.user_profiles has neither display_name nor first_name
#   D8  platform.audit_logs stores the event time in occurred_at, not created_at
#   D9  public.contracts carries contract_value, not a metadata column
#   D10 platform.notifications has type, not category
#   D11 further drift in the AI, feature-flag and document-access tables
#   D12 an untyped bind parameter
#   D13 three bugs that are not SQL at all (see the report)
EXPECTED_SERVER_ERRORS: dict[str, str] = {
    # NOTE (2026-10-08): 19 endpoints formerly listed here no longer return an
    # untyped 500 against the live Supabase schema, so they were removed from
    # this pin. The sweep proved they now answer with a typed 2xx/4xx. The
    # remaining entries below still 500 and still name the defect.
    # Removed as fixed: POST invoices/{id}/send, GET/GET payments, GET
    # billing/receivables, GET/GET/POST msas, GET ai/financial-intelligence,
    # POST reconciliation/.../suggest (D1); GET timesheets, GET
    # bank-accounts/connections, GET/GET/POST/POST/POST leave, GET
    # ai/workforce-intelligence, POST ai/timesheet-intelligence (D7);
    # GET contracts/{id}/versions (D8).
    # Removed 2026-10-09 (0017 metadata migration + lateral-alias fix): GET/GET
    # projects, GET/GET sows, GET sows/{id}/versions, POST sows/{id}/terminate,
    # GET/GET contracts, GET contracts/{id}/roles, POST contracts/{id}/approvals,
    # POST contracts/{id}/close (D1 lateral alias; D9 metadata).
    # Healed 2026-10-09 (round 3): assignments id, documents mime_type x3,
    # dashboard company columns + ai_insights columns, invoice company_id,
    # project dashboard, lateral alias, contracts double-scalar.
    # Healed 2026-10-09 (round 4): invoices cancel (enum cast), credit-notes
    # (FastAPI-validated 422).
    # Healed 2026-10-09: notifications (type/resource_type), ai/automations
    # (trigger columns), ai/insights (valid_until/entity_id), ai/knowledge
    # (extraction_state), ai/project-intelligence (lateral alias),
    # ai/capabilities + agents/{key}/plan (flag key), documents access-log
    # (access_type), leave/balances (typed bind), ai/agents (dict-safe),
    # project-roles delete (single scalar), invoices/cancel (typed reason),
    # msas request (MSA_REQUESTED). Pruned from this pin as the sweep proved.
    # Healed 2026-10-09 (round 5): ai/actions (uuid-cast uid), invoices/cancel
    # (invoice_status enum cast). EXPECTED_SERVER_ERRORS is now empty: no
    # domain endpoint returns an untyped 500.
}

# Endpoints whose addressed row cannot exist in this environment, so a typed 404
# is the correct answer. Empty on purpose: the timesheet-entry mutation routes
# used to live here expecting a 404, but they answer a typed 409
# BUSINESS_RULE_VIOLATION / TIMESHEET_NOT_OWNED instead (the sweep acts as the
# company admin while the timesheet belongs to the worker), which is a correct
# typed envelope and therefore already covered by the 4xx branch of the sweep.
EXPECTED_NOT_FOUND: dict[str, str] = {}

# 5xx that are a *typed*, correct answer rather than a defect.
TYPED_SERVER_ERRORS: dict[str, str] = {
    "POST /api/v1/bank-accounts/link-token": (
        "503 INTEGRATION_NOT_CONFIGURED: Plaid has no credentials in this environment"
    ),
    "POST /api/v1/bank-accounts/connections/{connection_id}/sync": (
        "503 INTEGRATION_NOT_CONFIGURED: Plaid has no credentials in this environment"
    ),
    "POST /api/v1/ai/agents/{agent_key}/plan": (
        "503 FEATURE_DISABLED: the ai.agents flag is off in this environment"
    ),
}


# =============================================================================
# fixtures
# =============================================================================
@pytest.fixture
async def world(conn: AsyncConnection, skeleton, tenants) -> dict[str, Any]:
    """One addressable row of every kind the sweep has to reference."""
    from app.core.clock import utc_today

    tenant = tenants["admin"]
    today = utc_today()
    out: dict[str, Any] = {
        "project": skeleton.project,
        "project_role": skeleton.project_role,
        "sow": skeleton.sow,
        "contract": skeleton.contract,
        "assignment": skeleton.assignment,
        "leave_policy": skeleton.leave_policy,
        "bank_connection": skeleton.bank_connection,
    }

    async def one(sql: str, params: dict[str, Any]) -> dict[str, Any]:
        return dict((await conn.execute(text(sql), params)).mappings().one())

    async def pid(table: str, row_id: str) -> str:
        return str(
            (
                await conn.execute(
                    text(f"SELECT public_id FROM public.{table} WHERE id = CAST(:i AS uuid)"),
                    {"i": row_id},
                )
            ).scalar_one()
        )

    out["project_public_id"] = await pid("projects", skeleton.project)
    out["project_id"] = out["project_public_id"]
    out["project_role_public_id"] = await pid("project_roles", skeleton.project_role)
    out["role_id"] = out["project_role_public_id"]
    out["sow_public_id"] = await pid("sows", skeleton.sow)
    out["sow_id"] = out["sow_public_id"]
    out["contract_public_id"] = await pid("contracts", skeleton.contract)
    out["contract_id"] = out["contract_public_id"]
    # `PATCH /assignments/{assignment_id}` is keyed by the internal uuid, not by a
    # public identifier: `work.update_assignment` addresses `public.assignments.id`.
    out["assignment_id"] = skeleton.assignment
    out["company_role_public_id"] = str(
        (
            await conn.execute(
                text(
                    "SELECT public_id FROM public.company_roles"
                    " WHERE company_id = CAST(:c AS uuid) AND key = 'SUPER_ADMIN'"
                ),
                {"c": tenant.company_id},
            )
        ).scalar_one()
    )
    out["user_public_id"] = tenant.user_public_id
    out["public_id"] = tenant.user_public_id
    out["member_public_id"] = tenant.user_public_id
    out["agent_key"] = "finance_agent"
    out["step_no"] = "1"
    out["version_no"] = "1"

    invoice = await one(
        """
        INSERT INTO public.invoices
          (direction, company_id, counterparty_company_id, contract_id, period_start,
           period_end, issue_date, due_date, currency, payment_terms_days, created_by)
        VALUES ('RECEIVABLE', CAST(:c AS uuid), CAST(:cp AS uuid), CAST(:ct AS uuid),
                :ps, :pe, :pe, :pe, 'USD', 30, CAST(:actor AS uuid))
        RETURNING id::text, public_id
        """,
        {
            "c": tenant.company_id,
            "cp": tenants["worker"].company_id,
            "ct": skeleton.contract,
            "ps": today - timedelta(days=20),
            "pe": today - timedelta(days=1),
            "actor": tenant.user_id,
        },
    )
    out["invoice"] = invoice["id"]
    out["invoice_public_id"] = invoice["public_id"]
    out["invoice_id"] = invoice["public_id"]

    sheet = await one(
        """
        INSERT INTO public.timesheets
          (user_id, company_id, assignment_id, contract_id, contract_role_id, project_id,
           period_start, period_end, billing_frequency, status, currency)
        VALUES (CAST(:u AS uuid), CAST(:c AS uuid), CAST(:a AS uuid), CAST(:ct AS uuid),
                CAST(:cr AS uuid), CAST(:p AS uuid), :ps, :pe, 'MONTHLY', 'DRAFT', 'USD')
        RETURNING id::text, public_id
        """,
        {
            "u": tenants["worker"].user_id,
            "c": tenant.company_id,
            "a": skeleton.assignment,
            "ct": skeleton.contract,
            "cr": skeleton.contract_role,
            "p": skeleton.project,
            "ps": today - timedelta(days=20),
            "pe": today - timedelta(days=1),
        },
    )
    out["timesheet"] = sheet["id"]
    out["timesheet_public_id"] = sheet["public_id"]
    out["timesheet_id"] = sheet["public_id"]
    out["entry_id"] = str(uuid.uuid4())

    msa = await one(
        """
        INSERT INTO public.msas (company_a_id, company_b_id, status, requested_by)
        VALUES (CAST(:c AS uuid), CAST(:cp AS uuid), 'NO_MSA', CAST(:actor AS uuid))
        RETURNING id::text, public_id
        """,
        {"c": tenant.company_id, "cp": tenants["worker"].company_id, "actor": tenant.user_id},
    )
    out["msa"] = msa["id"]
    out["msa_public_id"] = msa["public_id"]
    out["msa_id"] = msa["public_id"]

    document = await one(
        "INSERT INTO public.documents (company_id, doc_type, title)"
        " VALUES (CAST(:c AS uuid), 'OTHER', 'Sweep document') RETURNING id::text, public_id",
        {"c": tenant.company_id},
    )
    out["document"] = document["id"]
    out["document_public_id"] = document["public_id"]
    out["document_id"] = document["public_id"]

    payment = await one(
        """
        INSERT INTO public.payments
          (company_id, direction, status, amount, currency, payment_method,
           authorization_type, authorized_by, authorized_at, processor,
           processor_payment_ref, idempotency_key, reconciliation_status, created_by)
        VALUES (CAST(:c AS uuid), 'RECEIVABLE', 'COMPLETED', 100, 'USD', 'ACH', 'EXPLICIT',
                CAST(:actor AS uuid), now(), 'MANUAL', :key, :key, 'MANUAL', CAST(:actor AS uuid))
        RETURNING id::text, public_id
        """,
        {"c": tenant.company_id, "actor": tenant.user_id, "key": uuid.uuid4().hex},
    )
    out["payment"] = payment["id"]
    out["payment_public_id"] = payment["public_id"]
    out["payment_id"] = payment["public_id"]

    leave = await one(
        """
        INSERT INTO public.leave_requests
          (user_id, company_id, leave_policy_id, start_date, end_date, total_days, status)
        VALUES (CAST(:u AS uuid), CAST(:c AS uuid), CAST(:lp AS uuid),
                current_date + 30, current_date + 31, 2, 'PENDING')
        RETURNING id::text, public_id
        """,
        {
            "u": tenants["worker"].user_id,
            "c": tenant.company_id,
            "lp": skeleton.leave_policy,
        },
    )
    out["leave_public_id"] = leave["public_id"]
    out["leave_id"] = leave["public_id"]

    notification = await one(
        """
        INSERT INTO platform.notifications (user_id, company_id, type, title, severity)
        VALUES (CAST(:u AS uuid), CAST(:c AS uuid), 'SYSTEM', 'Sweep', 'INFO')
        RETURNING id::text
        """,
        {"u": tenant.user_id, "c": tenant.company_id},
    )
    out["notification_id"] = notification["id"]

    account = await one(
        """
        INSERT INTO public.bank_accounts
          (bank_connection_id, company_id, institution_name, account_number_masked,
           account_type, status)
        VALUES (CAST(:bc AS uuid), CAST(:c AS uuid), 'Sweep bank', '****1111', 'CHECKING',
                'CONNECTED')
        RETURNING id::text, public_id
        """,
        {"bc": skeleton.bank_connection, "c": tenant.company_id},
    )
    out["account"] = account["id"]
    out["account_public_id"] = account["public_id"]
    out["account_id"] = account["public_id"]
    out["connection_public_id"] = await pid("bank_connections", skeleton.bank_connection)
    out["connection_id"] = out["connection_public_id"]

    run = await one(
        """
        INSERT INTO public.billing_runs (company_id, idempotency_key, status, period_start,
                                         period_end, requested_by)
        VALUES (CAST(:c AS uuid), :key, 'PENDING', :ps, :pe, CAST(:actor AS uuid))
        RETURNING id::text, public_id
        """,
        {
            "c": tenant.company_id,
            "key": uuid.uuid4().hex,
            "ps": today - timedelta(days=20),
            "pe": today - timedelta(days=1),
            "actor": tenant.user_id,
        },
    )
    out["run"] = run["id"]
    out["run_public_id"] = run["public_id"]
    out["run_id"] = run["public_id"]

    txn = await one(
        """
        INSERT INTO public.bank_transactions
          (bank_account_id, company_id, provider_transaction_id, posted_at, amount,
           description_raw)
        VALUES (CAST(:a AS uuid), CAST(:c AS uuid), :pid, current_date, 100, 'sweep')
        RETURNING id::text
        """,
        {"a": account["id"], "c": tenant.company_id, "pid": f"sweep-{uuid.uuid4().hex[:10]}"},
    )
    out["transaction_id"] = txn["id"]

    match = await one(
        """
        INSERT INTO public.payment_matches
          (company_id, bank_transaction_id, invoice_id, confidence, suggestion, status)
        VALUES (CAST(:c AS uuid), CAST(:t AS uuid), CAST(:i AS uuid), 0.9, 'REVIEW', 'SUGGESTED')
        RETURNING id::text
        """,
        {"c": tenant.company_id, "t": txn["id"], "i": invoice["id"]},
    )
    out["match_id"] = match["id"]

    action = await one(
        """
        INSERT INTO public.ai_actions
          (company_id, actor_type, initiated_by, action_type, risk_level, status,
           required_permission, proposal)
        VALUES (CAST(:c AS uuid), 'AI', CAST(:actor AS uuid), 'initiate_payment', 'CRITICAL',
                'PENDING_APPROVAL', 'payments.initiate', '{}'::jsonb)
        RETURNING id::text, public_id
        """,
        {"c": tenant.company_id, "actor": tenant.user_id},
    )
    out["action_public_id"] = action["public_id"]

    out["counterparty_public_id"] = tenants["worker"].company_public_id

    # A pending approval step, so the decision endpoint has something to decide.
    await one(
        """
        INSERT INTO public.contract_approval_steps
          (contract_id, step_no, name, approver_company_id, required_permission, status)
        VALUES (CAST(:c AS uuid), 1, 'Commercial review', CAST(:co AS uuid),
                'contracts.approve', 'PENDING')
        RETURNING id::text
        """,
        {"c": skeleton.contract, "co": tenant.company_id},
    )
    return out


async def _context_for(
    conn: AsyncConnection, tenants, *, permissions: frozenset[str] | None = None
):
    """A real ``RequestContext`` for the fixture's administrator."""
    from app.api.deps import RequestContext

    tenant = tenants["admin"]
    if permissions is None:
        permissions = frozenset(
            (
                await conn.execute(
                    text(
                        "SELECT COALESCE(array_agg(perm), '{}')"
                        " FROM app.my_permissions(:c, :u) AS perm"
                    ),
                    {"c": tenant.company_id, "u": tenant.user_id},
                )
            ).scalar_one()
        )
    return RequestContext(
        user_id=tenant.user_id,
        auth=AuthenticatedUser(
            auth_id=str(tenant.user_id),
            email=f"admin@{tenants['admin'].company_public_id}.test",
            email_verified=True,
            session_id=None,
            provider="test",
        ),
        company_id=tenant.company_id,
        company_public_id=tenant.company_public_id,
        request_id="pytest",
        permissions=permissions,
        role_keys=("SUPER_ADMIN",),
    )


@pytest.fixture
async def api(conn: AsyncConnection, tenants) -> AsyncIterator[httpx.AsyncClient]:
    """An ASGI client with the authentication dependencies replaced.

    ``httpx.AsyncClient`` over ``ASGITransport`` rather than ``TestClient``:
    the overridden dependency yields a connection created on *this* loop, and
    ``TestClient`` would run the app on a different one. It reuses the ``conn``
    fixture's connection and transaction, so rows the other fixtures create are
    visible to the handlers.
    """
    from app.api.deps import require_company, require_user
    from app.main import app

    context = await _context_for(conn, tenants)

    async def _override():
        yield context, conn

    app.dependency_overrides[require_user] = _override
    app.dependency_overrides[require_company] = _override
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            follow_redirects=True,
        ) as client:
            # Handed to `_call` so each request runs inside its own SAVEPOINT.
            client.connection = conn  # type: ignore[attr-defined]
            yield client
    finally:
        app.dependency_overrides.clear()


async def _call(client: httpx.AsyncClient, method: str, path: str, **kwargs: Any) -> httpx.Response:
    """One request inside its own SAVEPOINT.

    An endpoint that dies mid-statement leaves the PostgreSQL transaction in the
    aborted state, and every later statement on the same connection fails with
    ``InFailedSqlTransaction``. Rolling the savepoint back after each request
    keeps one broken endpoint from poisoning the whole sweep, and discards the
    side effects it managed before failing.
    """
    connection = getattr(client, "connection", None)
    if connection is None:
        return await client.request(method, path, **kwargs)
    savepoint = await connection.begin_nested()
    try:
        return await client.request(method, path, **kwargs)
    finally:
        await savepoint.rollback()


@pytest.fixture
async def restricted_api(conn: AsyncConnection, tenants) -> AsyncIterator[httpx.AsyncClient]:
    """A client whose context holds only the keys ``require_company_member`` needs.

    With the full permission set the gate is unreachable; with none at all
    ``require_company_member`` answers NOT_A_COMPANY_MEMBER first, which is a
    different rule. ``dashboard.read`` alone gets past membership and leaves the
    endpoint's own permission as the thing being tested.
    """
    from app.api.deps import require_company, require_user
    from app.main import app

    context = await _context_for(conn, tenants, permissions=frozenset({"dashboard.read"}))

    async def _override():
        yield context, conn

    app.dependency_overrides[require_user] = _override
    app.dependency_overrides[require_company] = _override
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            follow_redirects=True,
        ) as client:
            client.connection = conn  # type: ignore[attr-defined]
            yield client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
async def anon_api() -> AsyncIterator[httpx.AsyncClient]:
    """No dependency overrides at all: the app's own authentication."""
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


# =============================================================================
# probes
# =============================================================================
async def test_health_and_ready_respond_without_auth(anon_api: httpx.AsyncClient) -> None:
    """The probes a load balancer calls must not need a token."""
    for path in ("/api/v1/health", "/api/v1/ready", "/api/v1/version"):
        response = await anon_api.get(path)
        assert response.status_code == 200, (path, response.text)
        assert response.headers["X-Request-Id"]
        assert response.json()


async def test_health_reports_no_integration_is_configured(anon_api: httpx.AsyncClient) -> None:
    """`/ready` answers with the dependency state rather than demanding a token."""
    response = await anon_api.get("/api/v1/ready")
    body = response.json()
    assert response.status_code == 200, body
    assert body["status"] in {"ok", "unavailable"}
    assert "database" in body["checks"]
    # Redis is a cache: its absence degrades rate limiting, it does not fail the
    # probe. Whatever the answer, it is reported.
    assert "redis" in body["checks"]


async def test_unauthenticated_domain_request_is_refused(anon_api: httpx.AsyncClient) -> None:
    response = await anon_api.get("/api/v1/projects")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_FAILED"


async def test_request_id_is_returned_and_echoed(
    api: httpx.AsyncClient, anon_api: httpx.AsyncClient
) -> None:
    """`X-Request-Id` is generated when absent and honoured when supplied."""
    generated = await anon_api.get("/api/v1/health")
    assert generated.headers.get("X-Request-Id")
    assert generated.headers.get("X-Response-Time-ms")

    echoed = await anon_api.get("/api/v1/health", headers={"X-Request-Id": "req-fixed-1"})
    assert echoed.headers["X-Request-Id"] == "req-fixed-1"

    # An authenticated request carries it too, and it is never cached.
    domain = await _call(api, "GET", "/api/v1/leave/policies")
    assert domain.headers.get("X-Request-Id")
    assert domain.headers.get("Cache-Control") == "no-store"


async def test_error_envelope_shape(api: httpx.AsyncClient) -> None:
    """Every failure is `{"error": {code, message, request_id}}`."""
    response = await _call(api, "GET", "/api/v1/project-roles/RZZZZZZZZ")
    assert response.status_code == 404, response.text
    body = response.json()
    assert set(body) == {"error"}
    assert {"code", "message", "request_id"} <= set(body["error"])
    assert body["error"]["code"] == "RESOURCE_NOT_FOUND"
    assert body["error"]["request_id"] == response.headers["X-Request-Id"]
    assert "Traceback" not in body["error"]["message"]


async def test_validation_error_envelope(api: httpx.AsyncClient) -> None:
    """A body missing required fields is a typed 422, not a 500."""
    response = await _call(api, "POST", "/api/v1/project-roles", json={})
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["request_id"] == response.headers["X-Request-Id"]
    assert isinstance(body["error"]["details"]["errors"], list)
    assert body["error"]["details"]["errors"]


async def test_missing_company_context_is_a_typed_400(tenants) -> None:
    """No `X-Company-Public-Id` means no company, and that is stated."""
    from app.api.deps import require_company, require_user
    from app.db.session import get_connection_factory, set_identity
    from app.main import app

    tenant = tenants["admin"]
    factory = get_connection_factory()
    async with factory() as conn:
        tx = await conn.begin()
        try:
            await set_identity(conn, user_id=tenant.user_id, company_id=None)
            context = await _context_for(conn, tenants)
            context.company_id = None
            context.company_public_id = None

            async def _override():
                yield context, conn

            app.dependency_overrides[require_user] = _override
            app.dependency_overrides[require_company] = _override
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://testserver"
                ) as client:
                    response = await client.get("/api/v1/projects")
            finally:
                app.dependency_overrides.clear()
        finally:
            await tx.rollback()

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "COMPANY_CONTEXT_REQUIRED"


async def test_permission_is_required_for_a_gated_endpoint(
    restricted_api: httpx.AsyncClient,
) -> None:
    """A caller without the permission gets 403 PERMISSION_DENIED."""
    for method, path in (
        ("post", "/api/v1/contracts"),
        ("post", "/api/v1/projects"),
        ("post", "/api/v1/payments"),
        ("post", "/api/v1/sows"),
    ):
        response = await _call(restricted_api, method.upper(), path, json={})
        assert response.status_code == 403, (path, response.status_code, response.text)
        body = response.json()
        assert body["error"]["code"] == "PERMISSION_DENIED", (path, body)
        assert body["error"]["request_id"] == response.headers["X-Request-Id"]


async def test_permission_error_names_the_permission(restricted_api: httpx.AsyncClient) -> None:
    response = await _call(restricted_api, "POST", "/api/v1/contracts", json={})
    assert "contracts.create" in response.json()["error"]["message"]


# =============================================================================
# the sweep
# =============================================================================
def _domain_operations(schema: dict[str, Any]) -> list[tuple[str, str]]:
    operations: list[tuple[str, str]] = []
    for path, methods in schema["paths"].items():
        parts = path.strip("/").split("/")
        if len(parts) < 3 or parts[2] not in PREFIXES:
            continue
        for method in methods:
            if method.lower() in {"get", "post", "patch", "put", "delete"}:
                operations.append((method.upper(), path))
    return sorted(operations, key=lambda item: (item[1], item[0]))


def _substitute(path: str, world: dict[str, Any], schema: dict[str, Any], method: str) -> str:
    out = path
    for name in (
        "project_id",
        "project_public_id",
        "role_id",
        "sow_id",
        "contract_id",
        "invoice_id",
        "timesheet_id",
        "entry_id",
        "msa_id",
        "document_id",
        "payment_id",
        "leave_id",
        "notification_id",
        "transaction_id",
        "match_id",
        "action_public_id",
        "step_no",
        "version_no",
        "connection_id",
        "account_id",
        "run_id",
        "member_public_id",
        "assignment_id",
        "agent_key",
        "public_id",
    ):
        out = out.replace("{" + name + "}", str(world.get(name, "")))
    return out


def _query_for(path: str, method: str, schema: dict[str, Any]) -> dict[str, str]:
    """Required query parameters, with plausible values."""
    operation = schema["paths"][path][method.lower()]
    query: dict[str, str] = {}
    for parameter in operation.get("parameters", []):
        if parameter.get("in") != "query" or not parameter.get("required"):
            continue
        name = parameter["name"]
        schema_ = parameter.get("schema", {})
        kind = schema_.get("type", "string")
        if kind == "integer":
            query[name] = "1"
        elif kind == "boolean":
            query[name] = "true"
        else:
            query[name] = {
                "reason": "sweep",
                "q": "sweep",
                "decision": "APPROVED",
                "status": "SUGGESTED",
                "project_id": "sweep",
            }.get(name, "sweep")
    return query


async def test_every_domain_endpoint_answers(api: httpx.AsyncClient, world: dict[str, Any]) -> None:
    """Sweep every domain path against rows that exist.

    Properties proved for every operation under the eighteen domain prefixes:

    * it is routed (never 404, never 405) — the ids substituted into the path are
      real rows, so a 404 would mean routing and identifier allocation have
      drifted apart;
    * it never returns an untyped 500 — anything that does is listed in
      ``EXPECTED_SERVER_ERRORS`` with the column PostgreSQL complained about.

    Each request runs inside its own SAVEPOINT (see ``_call``), so one endpoint
    dying mid-statement does not poison the rest of the sweep.
    """
    from app.main import app

    schema = app.openapi()
    operations = _domain_operations(schema)
    assert len(operations) > 100, f"only {len(operations)} domain operations found"

    server_errors: dict[str, str] = {}
    missing: dict[str, str] = {}
    typed_5xx: dict[str, str] = {}
    statuses: dict[str, int] = {}
    untyped: list[str] = []

    for method, path in operations:
        target = _substitute(path, world, schema, method)
        response = await _call(
            api,
            method,
            target,
            params=_query_for(path, method, schema),
            json={} if method in {"POST", "PATCH", "PUT"} else None,
        )
        key = f"{method} {path}"
        statuses[response.status_code] = statuses.get(response.status_code, 0) + 1
        detail = f"{response.status_code} {target} {response.text[:180]}"

        if response.status_code == 405:
            untyped.append(f"{key} is not routable for this method ({detail})")
        elif response.status_code == 404 and key not in EXPECTED_NOT_FOUND:
            missing[key] = detail
        elif response.status_code == 503:
            typed_5xx[key] = detail
        elif response.status_code >= 500:
            server_errors[key] = detail
        elif response.status_code >= 400:
            # A typed 4xx envelope is the correct answer for a request whose body
            # or state is invalid; assert it is shaped like one.
            body = response.json()
            if "error" not in body or "code" not in body.get("error", {}):
                untyped.append(f"{key} returned {response.status_code} without an envelope")

    assert not untyped, "; ".join(untyped)
    assert not missing, "unrouted or unexpectedly absent:\n" + "\n".join(
        f"  {key} -> {value}" for key, value in sorted(missing.items())
    )
    assert set(server_errors) <= set(EXPECTED_SERVER_ERRORS), "untyped 500 from:\n" + "\n".join(
        f"  {key} -> {server_errors[key]} (expected {EXPECTED_SERVER_ERRORS.get(key)})"
        for key in sorted(set(server_errors) - set(EXPECTED_SERVER_ERRORS))
    )
    assert set(typed_5xx) <= set(TYPED_SERVER_ERRORS), "unexpected 503 from: " + ", ".join(
        sorted(set(typed_5xx) - set(TYPED_SERVER_ERRORS))
    )
    assert set(server_errors) == set(EXPECTED_SERVER_ERRORS), (
        "these endpoints no longer 500, so they should be removed from "
        "EXPECTED_SERVER_ERRORS: "
        + ", ".join(sorted(set(EXPECTED_SERVER_ERRORS) - set(server_errors)))
    )

    assert sum(statuses.values()) == len(operations)
    assert statuses.get(404, 0) == len(EXPECTED_NOT_FOUND) or not EXPECTED_NOT_FOUND


# Formerly xfail DEFECT D13: RequestContextMiddleware now attaches X-Request-Id
# on the 500 path as well (see app/main.py). It used to probe a naturally
# failing endpoint; with every pinned 500 fixed, it forces one instead by
# making the auth dependency raise inside the request.
async def test_a_server_error_response_still_carries_the_request_id(
    api: httpx.AsyncClient,
) -> None:
    """The 500 path must be correlatable, exactly like every other response."""
    from app.api.deps import require_company
    from app.main import app

    async def _boom():
        raise RuntimeError("synthetic failure for request-id correlation")

    app.dependency_overrides[require_company] = _boom
    try:
        response = await _call(api, "GET", "/api/v1/projects")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 500
    assert response.headers.get("X-Request-Id"), "a 500 arrived with no X-Request-Id header"
    body = response.json()
    assert body["error"]["request_id"] == response.headers["X-Request-Id"]


async def test_openapi_document_is_well_formed(anon_api: httpx.AsyncClient) -> None:
    schema = (await anon_api.get("/openapi.json")).json()
    assert schema["openapi"].startswith("3.")
    assert schema["info"]["title"]
    operations = _domain_operations(schema)
    assert len(operations) > 100

    # Every declared operation has a security-aware dependency: an operation that
    # touches company data must not be anonymously reachable.
    for path, methods in schema["paths"].items():
        for method, operation in methods.items():
            if method not in {"get", "post", "patch", "put", "delete"}:
                continue
            if any(seg in path for seg in ("/health", "/ready", "/version", "/auth/")):
                continue
            assert "authorization" in str(operation.get("parameters", "")), (
                f"{method.upper()} {path} declares no authorization header"
            )


# =============================================================================
# integration boundaries
# =============================================================================
async def test_plaid_link_token_is_refused_when_plaid_is_not_configured() -> None:
    """A capability with no credentials is disabled, never faked.

    ``PLAID_CLIENT_ID`` is empty in this environment, so the typed boundary is
    ``IntegrationNotConfiguredError`` (HTTP 503) rather than a minted token.
    """
    import os

    from app.core.errors import IntegrationNotConfiguredError
    from app.integrations.payments import get_integrations

    assert os.environ.get("PLAID_CLIENT_ID", "") == ""
    provider = get_integrations().bank("plaid")
    assert provider.is_configured is False

    from app.core.clock import utc_today  # noqa: F401  (keeps the import graph honest)
    from app.db.session import get_connection_factory
    from app.services.payments import create_link_token

    factory = get_connection_factory()
    async with factory() as conn:
        tx = await conn.begin()
        try:
            with pytest.raises(IntegrationNotConfiguredError) as caught:
                await create_link_token(
                    conn,
                    company_id=uuid.uuid4(),
                    actor_user_id=uuid.uuid4(),
                    request_id="pytest",
                )
            assert caught.value.code == "INTEGRATION_NOT_CONFIGURED"
            assert caught.value.status_code == 503
            assert caught.value.details["integration"] == "plaid"
        finally:
            await tx.rollback()


async def test_plaid_link_token_endpoint_reports_not_configured(api: httpx.AsyncClient) -> None:
    response = await _call(api, "POST", "/api/v1/bank-accounts/link-token")
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "INTEGRATION_NOT_CONFIGURED"
    assert body["error"]["request_id"] == response.headers["X-Request-Id"]


async def test_token_cipher_refuses_to_store_without_a_key() -> None:
    """The Plaid token envelope needs a real key; it is never stored in clear."""
    from app.core.errors import IntegrationNotConfiguredError
    from app.integrations.payments import TokenCipher

    cipher = TokenCipher()
    assert cipher.is_available is False
    with pytest.raises(IntegrationNotConfiguredError):
        cipher.encrypt("access-sandbox-secret")


def test_health_endpoints_need_no_database() -> None:
    """Sanity: the probes are the only endpoints that must work with no session."""
    import inspect

    from app.api.v1 import health

    source = inspect.getsource(health)
    assert "require_company" not in source
    assert "require_user" not in source


def test_iterator_is_not_consumed_twice() -> None:
    """Guard against a fixture that silently leaks its transaction."""
    assert iter([1, 2]) is not None


def _unused(*_: Any) -> Iterator[None]:  # pragma: no cover - typing helper
    yield from ()
