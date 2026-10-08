"""BILLING and PAYMENTS endpoints.

Router mounts:
  /invoices, /billing                       invoicing + the billing engine
  /payments, /bank-accounts, /bank-transactions, /reconciliation
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.api.deps import RequestContext, company_scope, require_permission
from app.core.clock import utc_today
from app.core.logging import get_logger
from app.schemas.common import AckResponse, Page, build_page, clamp_limit, decode_cursor
from app.schemas.finance import (
    AllocatePaymentRequest,
    BankAccountResponse,
    BankConnectionResponse,
    BankTransactionResponse,
    BillingRunRequest,
    BillingRunResponse,
    CreatePaymentRequestBody,
    CreditNoteRequest,
    ExchangePlaidTokenRequest,
    GenerateInvoiceRequest,
    InvoiceActionRequest,
    InvoicePreviewRequest,
    InvoicePreviewResponse,
    InvoiceResponse,
    LinkSessionResponse,
    MatchCandidateResponse,
    MatchDecisionRequest,
    MatchDecisionResponse,
    MatchResponse,
    PaymentRequestResponse,
    PaymentResponse,
    ReconciliationSummaryResponse,
    RecordPaymentRequest,
    SyncConnectionRequest,
    SyncResultResponse,
    VerifyAccountRequest,
)
from app.services import invoicing as billing_service
from app.services import payments as payment_service

router = APIRouter(tags=["billing"])
logger = get_logger(__name__)

InvoicesRead = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("invoices.read"))
]
InvoicesCreate = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("invoices.create"))
]
InvoicesSubmit = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("invoices.submit"))
]
InvoicesApprove = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("invoices.approve"))
]
InvoicesCancel = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("invoices.cancel"))
]
BillingRead = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("billing_runs.read"))
]
BillingExecute = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("billing_runs.execute"))
]
PaymentsRead = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("payments.read"))
]
PaymentsCreate = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("payments.create"))
]
PaymentsBank = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("payments.connect_bank"))
]
TransactionsRead = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("transactions.read"))
]
ReconciliationRead = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("reconciliation.read"))
]
ReconciliationManage = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("reconciliation.manage"))
]


def _cursor(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    try:
        return decode_cursor(raw)
    except ValueError:
        from app.core.errors import ValidationError

        raise ValidationError("Invalid cursor.", details={"field": "cursor"}) from None


# =============================================================================
# invoices
# =============================================================================
@router.get("/invoices", response_model=Page[InvoiceResponse], summary="List invoices")
async def list_invoices(
    ctx_and_conn: InvoicesRead,
    status_filter: str | None = Query(None, alias="status", max_length=32),
    project_id: str | None = Query(None, max_length=32),
    contract_id: str | None = Query(None, max_length=32),
    counterparty_company_id: str | None = Query(None, max_length=32),
    overdue_only: bool = Query(False),
    period_start: Annotated[date | None, Query(description="Start of the billing period")] = None,
    period_end: Annotated[date | None, Query(description="End of the billing period")] = None,
    q: str | None = Query(None, max_length=200),
    limit: int = Query(25, ge=1, le=100),
    cursor: str | None = Query(default=None, description="Filter by this date"),
) -> Page[Any]:
    ctx, conn = ctx_and_conn
    page_size = clamp_limit(limit, default=25, maximum=100)
    rows = await billing_service.list_invoices(
        conn,
        company_id=company_scope(ctx),
        status=status_filter,
        statuses=None,
        project_public_id=project_id,
        contract_public_id=contract_id,
        counterparty_company_id=counterparty_company_id,
        overdue_only=overdue_only,
        period_start=period_start,
        period_end=period_end,
        search=q,
        limit=page_size,
        cursor_keys=_cursor(cursor),
    )
    for row in rows:
        row.pop("id", None)
    return build_page(
        rows, limit=page_size, cursor_keys=("created_at", "public_id"), request_id=ctx.request_id
    )


@router.post(
    "/billing/generate",
    response_model=InvoiceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Run the billing engine for a contract period",
)
async def generate_invoice(
    ctx_and_conn: InvoicesCreate, payload: GenerateInvoiceRequest
) -> dict[str, Any]:
    """Bills approved and locked timesheets at the contract role's rate.

    The amounts are computed server-side from `contract_roles.rate`; a rate in the
    request body is not accepted at all, because the contract is authoritative.
    """
    ctx, conn = ctx_and_conn
    return await billing_service.generate_invoice(
        conn,
        company_id=company_scope(ctx),
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        contract_public_id=payload.contract_id,
        period_start=payload.period_start,
        period_end=payload.period_end,
        discount=payload.discount,
        adjustment=payload.adjustment,
        notes=payload.notes,
    )


@router.post(
    "/billing/preview",
    response_model=InvoicePreviewResponse,
    summary="Preview an invoice without creating it",
)
async def preview_invoice(
    ctx_and_conn: BillingRead, payload: InvoicePreviewRequest
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await billing_service.preview_invoice(
        conn,
        company_id=company_scope(ctx),
        contract_public_id=payload.contract_id,
        period_start=payload.period_start,
        period_end=payload.period_end,
    )


@router.post(
    "/billing/runs",
    response_model=BillingRunResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start a billing run",
)
async def start_billing_run(
    ctx_and_conn: BillingExecute, payload: BillingRunRequest
) -> dict[str, Any]:
    """Queues invoice generation for every billable contract in the period."""
    ctx, conn = ctx_and_conn
    from app.services import billing as engine

    idempotency_key = f"run:{company_scope(ctx)}:{payload.period_start}:{payload.period_end}"
    row = (
        (
            await conn.execute(
                text(
                    """
                    INSERT INTO public.billing_runs
                      (company_id, run_type, period_start, period_end, currency,
                       idempotency_key, requested_by, status, scope_json)
                    VALUES (CAST(:cid AS uuid), 'INVOICE_GENERATION', :start, :end,
                            :currency, :key, :actor, 'PENDING', CAST(:scope AS jsonb))
                    ON CONFLICT (idempotency_key) DO NOTHING
                    RETURNING public_id
                    """
                ),
                {
                    "cid": company_scope(ctx),
                    "start": payload.period_start,
                    "end": payload.period_end,
                    "currency": payload.currency,
                    "key": idempotency_key,
                    "actor": ctx.user_id,
                    "scope": '{"source":"api"}',
                },
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        existing = (
            (
                await conn.execute(
                    text("SELECT public_id FROM public.billing_runs WHERE idempotency_key = :key"),
                    {"key": idempotency_key},
                )
            )
            .mappings()
            .first()
        )
        if existing is None:
            from app.core.errors import ConflictError

            raise ConflictError("Could not start this billing run.")
        public_id = str(existing["public_id"])
    else:
        public_id = str(row["public_id"])
        engine.enqueue(str(public_id), company_id=company_scope(ctx))

    return await _billing_run(conn, company_id=company_scope(ctx), public_id=public_id)


async def _billing_run(
    conn: AsyncConnection, *, company_id: uuid.UUID, public_id: str
) -> dict[str, Any]:
    row = (
        (
            await conn.execute(
                text(
                    """
                    SELECT br.public_id, br.run_type, br.status, br.period_start,
                           br.period_end, br.contracts_scanned, br.invoices_created,
                           br.invoices_skipped, br.total_amount, br.currency,
                           br.started_at, br.finished_at, br.error_details, br.created_at
                      FROM public.billing_runs br
                     WHERE br.public_id = :pid AND br.company_id = :cid
                    """
                ),
                {"pid": public_id, "cid": company_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        from app.core.errors import ResourceNotFoundError

        raise ResourceNotFoundError("Billing run not found.")
    return dict(row)


@router.get("/billing/runs", response_model=Page[BillingRunResponse], summary="List billing runs")
async def list_billing_runs(
    ctx_and_conn: BillingRead,
    limit: int = Query(25, ge=1, le=100),
    cursor: str | None = Query(default=None, description="Filter by this date"),
) -> Page[Any]:
    ctx, conn = ctx_and_conn
    page_size = clamp_limit(limit, default=25, maximum=100)
    keys = _cursor(cursor)
    where = ["br.company_id = :cid"]
    params: dict[str, Any] = {"cid": company_scope(ctx), "limit": page_size + 1}
    if keys.get("created_at"):
        where.append("(br.created_at, br.public_id) < (:cur_created, :cur_public)")
        params["cur_created"] = keys["created_at"]
        params["cur_public"] = keys["public_id"]

    rows = (
        (
            await conn.execute(
                text(
                    f"""
                    SELECT br.public_id, br.run_type, br.status, br.period_start,
                           br.period_end, br.contracts_scanned, br.invoices_created,
                           br.invoices_skipped, br.total_amount, br.currency,
                           br.started_at, br.finished_at, br.error_details, br.created_at
                      FROM public.billing_runs br
                     WHERE {" AND ".join(where)}
                     ORDER BY br.created_at DESC, br.public_id DESC
                     LIMIT :limit
                    """  # noqa: S608
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    return build_page(
        [dict(r) for r in rows],
        limit=page_size,
        cursor_keys=("created_at", "public_id"),
        request_id=ctx.request_id,
    )


@router.get(
    "/billing/runs/{run_id}", response_model=BillingRunResponse, summary="Get a billing run"
)
async def get_billing_run(ctx_and_conn: BillingRead, run_id: str) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await _billing_run(conn, company_id=company_scope(ctx), public_id=run_id)


@router.get(
    "/billing/receivables",
    summary="Receivables, aging and collections forecast",
)
async def receivables_summary(ctx_and_conn: BillingRead) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await billing_service.receivables_summary(conn, company_id=company_scope(ctx))


@router.get("/invoices/{invoice_id}", response_model=InvoiceResponse, summary="Get an invoice")
async def get_invoice(ctx_and_conn: InvoicesRead, invoice_id: str) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await billing_service.get_invoice(
        conn, company_id=company_scope(ctx), public_id=invoice_id
    )


@router.post(
    "/invoices/{invoice_id}/submit",
    response_model=InvoiceResponse,
    summary="Submit an invoice for approval",
)
async def submit_invoice(
    ctx_and_conn: InvoicesSubmit, invoice_id: str, payload: InvoiceActionRequest | None = None
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await billing_service.submit_for_approval(
        conn,
        company_id=company_scope(ctx),
        public_id=invoice_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        notes=payload.notes if payload else None,
    )


@router.post(
    "/invoices/{invoice_id}/approvals/{step_no}",
    response_model=InvoiceResponse,
    summary="Approve or reject an invoice",
)
async def decide_invoice_approval(
    ctx_and_conn: InvoicesApprove,
    invoice_id: str,
    step_no: int,
    decision: str = Query(..., pattern="^(APPROVED|REJECTED)$"),
    payload: InvoiceActionRequest | None = None,
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await billing_service.decide_approval(
        conn,
        company_id=company_scope(ctx),
        public_id=invoice_id,
        step_no=step_no,
        decision=decision,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        notes=payload.notes if payload else None,
    )


@router.post(
    "/invoices/{invoice_id}/send",
    response_model=InvoiceResponse,
    summary="Mark an approved invoice as issued",
)
async def send_invoice(
    ctx_and_conn: InvoicesApprove, invoice_id: str, payload: InvoiceActionRequest | None = None
) -> dict[str, Any]:
    """Records that the approved invoice was delivered to the customer."""
    ctx, conn = ctx_and_conn
    from app.services import audit

    invoice = await billing_service.get_invoice(
        conn, company_id=company_scope(ctx), public_id=invoice_id
    )
    if invoice["status"] != "APPROVED":
        from app.core.errors import InvalidStateTransitionError

        raise InvalidStateTransitionError(
            "Only an approved invoice can be sent.",
            details={"status": invoice["status"]},
        )
    await audit.record(
        conn,
        action="invoice.sent",
        resource_type="invoice",
        resource_public_id=invoice_id,
        company_id=company_scope(ctx),
        actor_user_id=ctx.user_id,
        new_values={"status": "APPROVED", "sent": True},
        reason=payload.notes if payload else None,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
    )
    return await billing_service.get_invoice(
        conn, company_id=company_scope(ctx), public_id=invoice_id
    )


@router.post(
    "/invoices/{invoice_id}/dispute",
    response_model=InvoiceResponse,
    summary="Mark an invoice as disputed",
)
async def dispute_invoice(
    ctx_and_conn: InvoicesApprove, invoice_id: str, payload: InvoiceActionRequest
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await billing_service.transition_invoice(
        conn,
        company_id=company_scope(ctx),
        public_id=invoice_id,
        target="DISPUTED",
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        reason=payload.reason,
    )


@router.post(
    "/invoices/{invoice_id}/cancel",
    response_model=InvoiceResponse,
    summary="Cancel an invoice",
)
async def cancel_invoice(
    ctx_and_conn: InvoicesCancel, invoice_id: str, payload: InvoiceActionRequest
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await billing_service.cancel_invoice(
        conn,
        company_id=company_scope(ctx),
        public_id=invoice_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        reason=payload.reason or "Cancelled by owner",
    )


@router.post(
    "/invoices/{invoice_id}/credit-notes",
    response_model=InvoiceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Issue a credit note",
)
async def issue_credit_note(
    ctx_and_conn: InvoicesCreate, invoice_id: str, payload: CreditNoteRequest
) -> dict[str, Any]:
    # Validated by FastAPI: an invalid body is a typed 422, never a 500 from a
    # manual model_validate call.
    ctx, conn = ctx_and_conn
    return await billing_service.issue_credit_note(
        conn,
        company_id=company_scope(ctx),
        public_id=invoice_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        amount=payload.amount,
        reason=payload.reason,
    )


@router.post(
    "/invoices/{invoice_id}/validate",
    summary="AI-assisted pre-approval validation",
)
async def validate_invoice(ctx_and_conn: InvoicesRead, invoice_id: str) -> dict[str, Any]:
    """Deterministic checks that run before approval, plus AI commentary when a
    provider is configured. Never blocks: warnings are advisory except the
    deterministic ones the database already enforces."""
    ctx, conn = ctx_and_conn
    from app.services import ai_domain

    invoice = await billing_service.get_invoice(
        conn, company_id=company_scope(ctx), public_id=invoice_id
    )
    findings = await ai_domain.validate_invoice(
        conn, company_id=company_scope(ctx), invoice=invoice
    )
    commentary = None
    if ctx.can("ai.contract_intelligence"):
        commentary = await ai_domain.explain_invoice_warnings(
            conn, company_id=company_scope(ctx), invoice=invoice, findings=findings
        )
    return {
        "invoice_id": invoice_id,
        "invoice_number": invoice.get("invoice_number"),
        "status": invoice["status"],
        "findings": findings,
        "commentary": commentary,
    }


# =============================================================================
# payments
# =============================================================================
@router.get("/payments", response_model=Page[PaymentResponse], summary="List payments")
async def list_payments(
    ctx_and_conn: PaymentsRead,
    status_filter: str | None = Query(None, alias="status", max_length=32),
    direction: str | None = Query(None, pattern="^(RECEIVABLE|PAYABLE)$"),
    counterparty_company_id: str | None = Query(None, max_length=32),
    unmatched_only: bool = Query(False),
    limit: int = Query(25, ge=1, le=100),
    cursor: str | None = Query(default=None, description="Filter by this date"),
) -> Page[Any]:
    ctx, conn = ctx_and_conn
    page_size = clamp_limit(limit, default=25, maximum=100)
    rows = await payment_service.list_payments(
        conn,
        company_id=company_scope(ctx),
        status=status_filter,
        direction=direction,
        counterparty_company_id=counterparty_company_id,
        unmatched_only=unmatched_only,
        limit=page_size,
        cursor_keys=_cursor(cursor),
    )
    for row in rows:
        row.pop("id", None)
    return build_page(
        rows, limit=page_size, cursor_keys=("created_at", "public_id"), request_id=ctx.request_id
    )


@router.post(
    "/payments",
    response_model=PaymentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record a payment",
)
async def record_payment(
    ctx_and_conn: PaymentsCreate, payload: RecordPaymentRequest
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await payment_service.record_payment(
        conn,
        company_id=company_scope(ctx),
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        payload=payload.model_dump(),
    )


@router.get("/payments/{payment_id}", response_model=PaymentResponse, summary="Get a payment")
async def get_payment(ctx_and_conn: PaymentsRead, payment_id: str) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await payment_service.get_payment(
        conn, company_id=company_scope(ctx), public_id=payment_id
    )


@router.post(
    "/payments/{payment_id}/allocations",
    response_model=PaymentResponse,
    summary="Apply a payment to invoices",
)
async def allocate_payment(
    ctx_and_conn: PaymentsCreate, payment_id: str, payload: AllocatePaymentRequest
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await payment_service.allocate_payment(
        conn,
        company_id=company_scope(ctx),
        payment_public_id=payment_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        allocations=[a.model_dump() for a in payload.allocations],
        matched_by=payload.matched_by,
        confidence=payload.confidence,
    )


@router.get(
    "/payment-requests",
    response_model=Page[PaymentRequestResponse],
    summary="List payment requests",
)
async def list_payment_requests(
    ctx_and_conn: PaymentsRead,
    limit: int = Query(25, ge=1, le=100),
    cursor: str | None = Query(default=None, description="Filter by this date"),
) -> Page[Any]:
    ctx, conn = ctx_and_conn
    page_size = clamp_limit(limit, default=25, maximum=100)
    keys = _cursor(cursor)
    where = ["pr.company_id = :cid"]
    params: dict[str, Any] = {"cid": company_scope(ctx), "limit": page_size + 1}
    if keys.get("created_at"):
        where.append("(pr.created_at, pr.public_id) < (:cur_created, :cur_public)")
        params["cur_created"] = keys["created_at"]
        params["cur_public"] = keys["public_id"]

    rows = (
        (
            await conn.execute(
                text(
                    f"""
                    SELECT pr.public_id, pr.requester_company_id, pr.counterparty_company_id,
                           pr.amount, pr.currency, pr.status, pr.requested_due_date,
                           pr.notes, pr.created_at, pr.invoice_id::text AS invoice_id,
                           i.public_id AS invoice_public_id, i.invoice_number,
                           ru.public_id AS requested_by
                      FROM public.payment_requests pr
                      LEFT JOIN public.invoices i ON i.id = pr.invoice_id
                      LEFT JOIN public.users ru ON ru.id = pr.requested_by
                     WHERE {" AND ".join(where)}
                     ORDER BY pr.created_at DESC, pr.public_id DESC
                     LIMIT :limit
                    """  # noqa: S608
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    return build_page(
        [dict(r) for r in rows],
        limit=page_size,
        cursor_keys=("created_at", "public_id"),
        request_id=ctx.request_id,
    )


@router.post(
    "/payment-requests",
    response_model=PaymentRequestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Request payment from a counterparty",
)
async def create_payment_request(
    ctx_and_conn: PaymentsCreate, payload: CreatePaymentRequestBody
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    from app.services.lookup import resolve_company_public_id, resolve_scoped

    requester = await resolve_company_public_id(conn, payload.requester_company_id)
    invoice_id = None
    counterparty = None
    if payload.invoice_id:
        invoice = await resolve_scoped(conn, "invoices", payload.invoice_id, company_scope(ctx))
        invoice_id = invoice["id"]
        counterparty = invoice["counterparty_company_id"]

    row = (
        (
            await conn.execute(
                text(
                    """
                    INSERT INTO public.payment_requests
                      (company_id, requester_company_id, invoice_id,
                       counterparty_company_id, amount, currency, requested_by,
                       status, requested_due_date, notes)
                    VALUES (CAST(:cid AS uuid), CAST(:requester AS uuid),
                            CAST(:invoice AS uuid), CAST(:cp AS uuid), :amount, :ccy,
                            :actor, 'PENDING', :due, :notes)
                    RETURNING public_id
                    """
                ),
                {
                    "cid": company_scope(ctx),
                    "requester": requester,
                    "invoice": invoice_id,
                    "cp": counterparty,
                    "amount": payload.amount,
                    "ccy": payload.currency,
                    "actor": ctx.user_id,
                    "due": payload.requested_due_date,
                    "notes": payload.notes,
                },
            )
        )
        .mappings()
        .first()
    )
    return {
        "public_id": str(row["public_id"]),
        "requester_company_id": payload.requester_company_id,
        "counterparty_company_id": payload.invoice_id,
        "amount": payload.amount,
        "currency": payload.currency,
        "status": "PENDING",
        "requested_due_date": payload.requested_due_date,
        "notes": payload.notes,
        "created_at": utc_today(),
    }


# =============================================================================
# bank accounts, connections and transactions
# =============================================================================
@router.get(
    "/bank-accounts/connections",
    response_model=list[BankConnectionResponse],
    summary="List bank connections",
)
async def list_bank_connections(ctx_and_conn: PaymentsBank) -> list[dict[str, Any]]:
    ctx, conn = ctx_and_conn
    return await payment_service.list_bank_connections(conn, company_id=company_scope(ctx))


@router.post(
    "/bank-accounts/link-token",
    response_model=LinkSessionResponse,
    summary="Create a Plaid Link session",
)
async def create_link_token(ctx_and_conn: PaymentsBank) -> dict[str, Any]:
    """Mints the link token on the server. The Plaid secret never reaches the browser."""
    ctx, conn = ctx_and_conn
    return await payment_service.create_link_token(
        conn, company_id=company_scope(ctx), actor_user_id=ctx.user_id, request_id=ctx.request_id
    )


@router.post(
    "/bank-accounts/connections",
    response_model=BankConnectionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Complete a Plaid Link exchange",
)
async def exchange_public_token(
    ctx_and_conn: PaymentsBank, payload: ExchangePlaidTokenRequest
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await payment_service.exchange_public_token(
        conn,
        company_id=company_scope(ctx),
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        public_token=payload.public_token,
    )


@router.post(
    "/bank-accounts/connections/{connection_id}/sync",
    response_model=SyncResultResponse,
    summary="Import transactions for a connection",
)
async def sync_connection(
    ctx_and_conn: PaymentsBank, connection_id: str, payload: SyncConnectionRequest | None = None
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await payment_service.sync_connection(
        conn,
        company_id=company_scope(ctx),
        public_id=connection_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        start_date=payload.start_date if payload else None,
        end_date=payload.end_date if payload else None,
    )


@router.post(
    "/bank-accounts/connections/{connection_id}/disconnect",
    response_model=AckResponse,
    summary="Disconnect a bank connection",
)
async def disconnect_connection(
    ctx_and_conn: PaymentsBank,
    connection_id: str,
    reason: str = Query("Disconnected by owner", min_length=3, max_length=500),
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    await payment_service.disconnect_connection(
        conn,
        company_id=company_scope(ctx),
        public_id=connection_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        reason=reason,
    )
    return {"ok": True, "message": "Bank disconnected.", "request_id": ctx.request_id}


@router.get(
    "/bank-accounts", response_model=list[BankAccountResponse], summary="List bank accounts"
)
async def list_bank_accounts(
    ctx_and_conn: PaymentsRead, connection_id: str | None = Query(None, max_length=32)
) -> list[dict[str, Any]]:
    ctx, conn = ctx_and_conn
    return await payment_service.list_bank_accounts(
        conn, company_id=company_scope(ctx), connection_public_id=connection_id
    )


@router.post(
    "/bank-accounts/{account_id}/verify",
    response_model=BankAccountResponse,
    summary="Record an account ownership decision",
)
async def verify_bank_account(
    ctx_and_conn: PaymentsBank, account_id: str, payload: VerifyAccountRequest
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await payment_service.verify_bank_account(
        conn,
        company_id=company_scope(ctx),
        public_id=account_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        verified=payload.verified,
    )


@router.get(
    "/bank-transactions",
    response_model=Page[BankTransactionResponse],
    summary="List bank transactions",
)
async def list_transactions(
    ctx_and_conn: TransactionsRead,
    account_id: str | None = Query(None, max_length=32),
    match_status: str | None = Query(None, max_length=32),
    unmatched_only: bool = Query(False),
    date_from: Annotated[date | None, Query(description="Earliest posting date")] = None,
    date_to: Annotated[date | None, Query(description="Latest posting date")] = None,
    q: str | None = Query(None, max_length=200),
    min_amount: Annotated[Decimal | None, Query(description="Minimum absolute amount")] = None,
    max_amount: Annotated[Decimal | None, Query(description="Maximum absolute amount")] = None,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(default=None, description="Filter by this date"),
) -> Page[Any]:
    ctx, conn = ctx_and_conn
    page_size = clamp_limit(limit, default=50, maximum=200)
    rows = await payment_service.list_transactions(
        conn,
        company_id=company_scope(ctx),
        account_public_id=account_id,
        match_status=match_status,
        unmatched_only=unmatched_only,
        date_from=date_from,
        date_to=date_to,
        search=q,
        min_amount=min_amount,
        max_amount=max_amount,
        limit=page_size,
        cursor_keys=_cursor(cursor),
    )
    return build_page(
        rows, limit=page_size, cursor_keys=("posted_at", "id"), request_id=ctx.request_id
    )


# =============================================================================
# reconciliation
# =============================================================================
@router.get(
    "/reconciliation",
    response_model=Page[MatchResponse],
    summary="Reconciliation queue",
)
async def reconciliation_queue(
    ctx_and_conn: ReconciliationRead,
    status_filter: str | None = Query("SUGGESTED", alias="status", max_length=32),
    limit: int = Query(25, ge=1, le=100),
    cursor: str | None = Query(default=None, description="Filter by this date"),
) -> Page[Any]:
    ctx, conn = ctx_and_conn
    page_size = clamp_limit(limit, default=25, maximum=100)
    rows = await payment_service.reconciliation_queue(
        conn,
        company_id=company_scope(ctx),
        status=status_filter,
        limit=page_size,
        cursor_keys=_cursor(cursor),
    )
    return build_page(
        rows, limit=page_size, cursor_keys=("created_at", "id"), request_id=ctx.request_id
    )


@router.get(
    "/reconciliation/summary",
    response_model=ReconciliationSummaryResponse,
    summary="Reconciliation summary",
)
async def reconciliation_summary(
    ctx_and_conn: ReconciliationRead,
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await payment_service.reconciliation_summary(conn, company_id=company_scope(ctx))


@router.post(
    "/reconciliation/bank-transactions/{transaction_id}/suggest",
    response_model=list[MatchCandidateResponse],
    summary="Suggest invoice matches for a transaction",
)
async def suggest_matches(
    ctx_and_conn: ReconciliationRead, transaction_id: str
) -> list[dict[str, Any]]:
    """Rule engine plus optional AI commentary. Nothing is matched automatically."""
    ctx, conn = ctx_and_conn
    candidates = await payment_service.suggest_matches(
        conn,
        company_id=company_scope(ctx),
        transaction_id=transaction_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
    )
    if candidates and ctx.can("ai.financial_intelligence"):
        from app.services import ai_domain

        await ai_domain.annotate_match_candidates(
            conn,
            company_id=company_scope(ctx),
            transaction_id=transaction_id,
            candidates=candidates,
            can=ctx.can,
        )
    return candidates


@router.post(
    "/reconciliation/matches/{match_id}",
    response_model=MatchDecisionResponse,
    summary="Accept or reject a match",
)
async def decide_match(
    ctx_and_conn: ReconciliationManage, match_id: str, payload: MatchDecisionRequest
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await payment_service.decide_match(
        conn,
        company_id=company_scope(ctx),
        match_id=match_id,
        decision=payload.decision,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        notes=payload.notes,
        create_payment=payload.create_payment,
    )


@router.post("/reconciliation/scan", summary="Generate suggestions for every unmatched credit")
async def scan_unmatched(ctx_and_conn: ReconciliationRead) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    rows = await conn.execute(
        text(
            """
            SELECT t.id::text
              FROM public.bank_transactions t
             WHERE t.company_id = :cid
               AND t.amount < 0
               AND t.match_status IN ('UNMATCHED','SUGGESTED')
             ORDER BY t.posted_at DESC
             LIMIT 50
            """
        ),
        {"cid": company_scope(ctx)},
    )
    scanned = 0
    suggested = 0
    for row in rows.mappings().all():
        scanned += 1
        candidates = await payment_service.suggest_matches(
            conn,
            company_id=company_scope(ctx),
            transaction_id=str(row["id"]),
            actor_user_id=ctx.user_id,
            request_id=ctx.request_id,
        )
        suggested += len(candidates)
    return {"scanned": scanned, "suggestions": suggested}


# =============================================================================
# recurring schedules
# =============================================================================
@router.get("/payment-schedules", summary="List recurring payment schedules")
async def list_schedules(
    ctx_and_conn: PaymentsRead, limit: int = Query(50, ge=1, le=200)
) -> dict[str, Any]:
    from app.services import schedules as schedule_service

    ctx, conn = ctx_and_conn
    rows = await schedule_service.list_schedules(conn, company_id=company_scope(ctx), limit=limit)
    return {"data": rows, "request_id": ctx.request_id}


@router.post("/payment-schedules", status_code=201, summary="Create a recurring schedule")
async def create_schedule(ctx_and_conn: PaymentsCreate, payload: dict[str, Any]) -> dict[str, Any]:
    from app.services import schedules as schedule_service

    ctx, conn = ctx_and_conn
    return await schedule_service.create_schedule(
        conn,
        company_id=company_scope(ctx),
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        payload=payload,
    )


@router.get("/payment-schedules/{schedule_id}", summary="Read one schedule")
async def read_schedule(ctx_and_conn: PaymentsRead, schedule_id: str) -> dict[str, Any]:
    from app.services import schedules as schedule_service

    ctx, conn = ctx_and_conn
    return await schedule_service.get_schedule(
        conn, company_id=company_scope(ctx), public_id=schedule_id
    )


@router.post("/payment-schedules/{schedule_id}/pause", summary="Pause a schedule")
async def pause_schedule(ctx_and_conn: PaymentsCreate, schedule_id: str) -> dict[str, Any]:
    from app.services import schedules as schedule_service

    ctx, conn = ctx_and_conn
    return await schedule_service.set_schedule_status(
        conn,
        company_id=company_scope(ctx),
        public_id=schedule_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        status="PAUSED",
    )


@router.post("/payment-schedules/{schedule_id}/cancel", summary="Cancel a schedule")
async def cancel_schedule(ctx_and_conn: PaymentsCreate, schedule_id: str) -> dict[str, Any]:
    from app.services import schedules as schedule_service

    ctx, conn = ctx_and_conn
    return await schedule_service.set_schedule_status(
        conn,
        company_id=company_scope(ctx),
        public_id=schedule_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        status="CANCELLED",
    )


@router.post("/payment-schedules/run-due", summary="Create today's due occurrences")
async def run_due_schedules(ctx_and_conn: PaymentsCreate) -> dict[str, Any]:
    from app.services import schedules as schedule_service

    ctx, conn = ctx_and_conn
    result = await schedule_service.run_due_schedules(
        conn,
        company_id=company_scope(ctx),
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
    )
    return {**result, "request_id": ctx.request_id}


# =============================================================================
# processor webhooks (signature is the authentication: no user session)
# =============================================================================
@router.post("/payments/webhooks/{provider}", summary="Processor webhook ingest")
async def processor_webhook(provider: str, request: Request) -> dict[str, Any]:
    """Verify, persist idempotently, and fan out for background processing.

    Unverifiable bodies are rejected before anything is stored; redeliveries
    return the original receipt without re-processing.
    """
    from app.db.session import session_scope
    from app.services import webhooks as webhook_service

    raw = await request.body()
    signature = request.headers.get("stripe-signature") or request.headers.get("x-signature")
    async with session_scope() as conn:
        return await webhook_service.ingest_processor_webhook(
            conn,
            provider=provider,
            raw_body=raw,
            signature=signature,
            request_id=getattr(request.state, "request_id", ""),
        )
