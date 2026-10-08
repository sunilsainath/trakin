"""BILLING and PAYMENTS contracts: invoices, billing runs, payments, bank
connections, transactions and reconciliation."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PublicId = str
InvoiceStatus = Literal[
    "DRAFT",
    "PENDING",
    "SUBMITTED",
    "APPROVED",
    "PARTIALLY_PAID",
    "PAID",
    "OVERDUE",
    "DISPUTED",
    "REJECTED",
    "CANCELLED",
    "REFUNDED",
]
PaymentStatus = Literal[
    "SCHEDULED",
    "INITIATED",
    "PROCESSING",
    "COMPLETED",
    "FAILED",
    "PARTIALLY_REFUNDED",
    "REFUNDED",
    "CANCELLED",
]
MatchStatus = Literal["UNMATCHED", "SUGGESTED", "MATCHED", "PARTIALLY_MATCHED", "IGNORED"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# =============================================================================
# invoices
# =============================================================================
class GenerateInvoiceRequest(_Strict):
    contract_id: PublicId
    period_start: dt.date
    period_end: dt.date
    discount: Decimal = Field(default=Decimal("0"), ge=0)
    adjustment: Decimal = Field(default=Decimal("0"))
    notes: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def _period(self) -> GenerateInvoiceRequest:
        if self.period_end < self.period_start:
            raise ValueError("period_end must not precede period_start")
        return self


class InvoiceActionRequest(_Strict):
    reason: str | None = Field(default=None, max_length=2000)
    notes: str | None = Field(default=None, max_length=2000)


class CreditNoteRequest(_Strict):
    amount: Decimal = Field(..., gt=0)
    reason: str = Field(..., min_length=3, max_length=1000)


class InvoiceItemResponse(BaseModel):
    id: str
    description: str
    line_type: str
    quantity: Decimal
    unit: str
    unit_rate: Decimal
    subtotal: Decimal
    tax_rate: Decimal = Decimal("0")
    tax_total: Decimal = Decimal("0")
    total: Decimal
    currency: str = "USD"
    service_period_start: dt.date | None = None
    service_period_end: dt.date | None = None
    source_timesheet_id: str | None = None
    project_role_id: PublicId | None = None
    project_role_title: str | None = None


class InvoiceResponse(BaseModel):
    public_id: PublicId
    invoice_number: str | None = None
    direction: str = "RECEIVABLE"
    status: str
    company_id: PublicId
    contract_id: PublicId
    contract_title: str | None = None
    project_id: PublicId | None = None
    project_name: str | None = None
    sow_id: PublicId | None = None
    sow_title: str | None = None
    counterparty_company_id: PublicId | None = None
    counterparty_company_name: str | None = None
    counterparty_user_id: PublicId | None = None
    counterparty_user_name: str | None = None
    role_ids: list[PublicId] = Field(default_factory=list)
    period_start: dt.date
    period_end: dt.date
    issue_date: dt.date | None = None
    due_date: dt.date
    currency: str = "USD"
    subtotal: Decimal = Decimal("0")
    tax_total: Decimal = Decimal("0")
    total_amount: Decimal = Decimal("0")
    amount_paid: Decimal = Decimal("0")
    amount_disputed: Decimal = Decimal("0")
    balance_due: Decimal = Decimal("0")
    allocated_total: Decimal = Decimal("0")
    payment_terms_days: int = 30
    msa_required: bool = False
    msa_block_reason: str | None = None
    disputed_reason: str | None = None
    rejected_reason: str | None = None
    notes: str | None = None
    terms_snapshot: dict[str, Any] = Field(default_factory=dict)
    locked: bool = False
    version: int = 1
    items: list[InvoiceItemResponse] = Field(default_factory=list)
    allocations: list[dict[str, Any]] = Field(default_factory=list)
    approvals: list[dict[str, Any]] = Field(default_factory=list)
    history: list[dict[str, Any]] = Field(default_factory=list)
    allowed_transitions: list[str] = Field(default_factory=list)
    item_count: int = 0
    submitted_at: dt.datetime | None = None
    approved_at: dt.datetime | None = None
    paid_at: dt.datetime | None = None
    created_at: dt.datetime
    updated_at: dt.datetime


class InvoicePreviewRequest(_Strict):
    contract_id: PublicId
    period_start: dt.date
    period_end: dt.date


class InvoicePreviewResponse(BaseModel):
    contract_id: PublicId
    contract_status: str
    period_start: dt.date
    period_end: dt.date
    currency: str
    subtotal: Decimal
    tax_total: Decimal
    total: Decimal
    items: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    msa_required: bool = False


class BillingRunRequest(_Strict):
    contract_id: PublicId | None = None
    period_start: dt.date
    period_end: dt.date
    currency: str = "USD"


class BillingRunResponse(BaseModel):
    public_id: PublicId
    run_type: str
    status: str
    period_start: dt.date | None = None
    period_end: dt.date | None = None
    contracts_scanned: int = 0
    invoices_created: int = 0
    invoices_skipped: int = 0
    total_amount: Decimal = Decimal("0")
    currency: str = "USD"
    started_at: dt.datetime | None = None
    finished_at: dt.datetime | None = None
    error_details: Any = None
    created_at: dt.datetime


# =============================================================================
# payments
# =============================================================================
class RecordPaymentRequest(_Strict):
    direction: Literal["RECEIVABLE", "PAYABLE"] = "RECEIVABLE"
    amount: Decimal = Field(..., gt=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    counterparty_company_id: PublicId | None = None
    counterparty_name: str | None = Field(default=None, max_length=200)
    bank_account_id: PublicId | None = None
    payment_method: Literal["ACH", "WIRE", "CARD", "CHECK", "CASH", "CRYPTO", "OTHER"] = "ACH"
    fee_amount: Decimal = Field(default=Decimal("0"), ge=0)
    scheduled_for: dt.date | None = None
    processor: str = Field(default="manual", max_length=32)
    idempotency_key: str | None = Field(default=None, max_length=200)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AllocationRequest(_Strict):
    invoice_id: PublicId
    amount: Decimal = Field(..., gt=0)


class AllocatePaymentRequest(_Strict):
    allocations: list[AllocationRequest] = Field(..., min_length=1, max_length=200)
    matched_by: Literal["MANUAL", "AI_SUGGESTED", "AUTO_ACCEPTED", "SYSTEM"] = "MANUAL"
    confidence: Decimal | None = Field(default=None, ge=0, le=1)


class PaymentAllocationResponse(BaseModel):
    id: str
    invoice_id: str | None = None
    invoice_public_id: PublicId | None = None
    invoice_number: str | None = None
    amount: Decimal
    currency: str
    allocation_type: str = "INVOICE"
    confidence: Decimal | None = None
    matched_by: str = "MANUAL"
    confirmed_at: dt.datetime | None = None


class PaymentResponse(BaseModel):
    public_id: PublicId
    direction: str
    status: str
    amount: Decimal
    currency: str = "USD"
    fee_amount: Decimal = Decimal("0")
    net_amount: Decimal | None = None
    payment_method: str = "ACH"
    counterparty_company_id: PublicId | None = None
    counterparty_company_name: str | None = None
    counterparty_name: str | None = None
    bank_account_id: PublicId | None = None
    account_number_masked: str | None = None
    payment_account_id: PublicId | None = None
    scheduled_for: dt.date | None = None
    initiated_at: dt.datetime | None = None
    completed_at: dt.datetime | None = None
    failed_at: dt.datetime | None = None
    failure_reason: str | None = None
    authorization_type: str = "EXPLICIT"
    processor: str = "MANUAL"
    processor_payment_ref: str | None = None
    reconciliation_status: str = "PENDING"
    bank_transaction_id: str | None = None
    allocated_total: Decimal = Decimal("0")
    allocation_count: int = 0
    allocations: list[PaymentAllocationResponse] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: dt.datetime
    updated_at: dt.datetime


class PaymentRequestResponse(BaseModel):
    public_id: PublicId
    requester_company_id: PublicId
    counterparty_company_id: PublicId | None = None
    invoice_id: str | None = None
    invoice_public_id: PublicId | None = None
    invoice_number: str | None = None
    amount: Decimal
    currency: str = "USD"
    status: str
    requested_by: PublicId | None = None
    requested_due_date: dt.date | None = None
    notes: str | None = None
    created_at: dt.datetime


class CreatePaymentRequestBody(_Strict):
    requester_company_id: PublicId
    invoice_id: PublicId | None = None
    amount: Decimal = Field(..., gt=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    requested_due_date: dt.date | None = None
    notes: str | None = Field(default=None, max_length=2000)


# =============================================================================
# bank accounts and transactions
# =============================================================================
class ExchangePlaidTokenRequest(_Strict):
    public_token: str = Field(..., min_length=10, max_length=4096)
    institution_name: str | None = Field(default=None, max_length=200)


class LinkSessionResponse(BaseModel):
    link_token: str
    expires_at: str | None = None
    provider: str = "plaid"


class BankConnectionResponse(BaseModel):
    public_id: PublicId
    provider: str = "PLAID"
    institution_name: str | None = None
    institution_id: str | None = None
    status: str
    verification_state: str = "UNVERIFIED"
    ownership_verified: bool = False
    consent_expires_at: dt.datetime | None = None
    last_synced_at: dt.datetime | None = None
    transaction_count: int = 0
    account_count: int = 0
    owner_user_id: PublicId | None = None
    created_at: dt.datetime
    error_detail: dict[str, Any] | None = None


class BankAccountResponse(BaseModel):
    public_id: PublicId
    connection_public_id: PublicId
    institution_name: str
    name: str | None = None
    account_number_masked: str
    account_type: str
    currency: str = "USD"
    status: str
    verification_state: str = "UNVERIFIED"
    is_primary: bool = False
    available_balance: Decimal | None = None
    current_balance: Decimal | None = None
    connected_at: dt.datetime | None = None
    verified_at: dt.datetime | None = None
    last_synced_at: dt.datetime | None = None
    transaction_count: int = 0
    unmatched_count: int = 0


class VerifyAccountRequest(_Strict):
    verified: bool


class SyncConnectionRequest(_Strict):
    start_date: dt.date | None = None
    end_date: dt.date | None = None


class SyncResultResponse(BaseModel):
    connection_id: PublicId
    fetched: int = 0
    inserted: int = 0
    cursor: str | None = None
    has_more: bool = False


class BankTransactionResponse(BaseModel):
    id: str
    account_id: str
    account_public_id: PublicId
    account_number_masked: str | None = None
    institution_name: str | None = None
    posted_at: dt.datetime
    authorized_at: dt.datetime | None = None
    amount: Decimal
    currency: str = "USD"
    direction: str
    description_raw: str | None = None
    merchant_name: str | None = None
    normalized_description: str | None = None
    category: str | None = None
    is_pending: bool = False
    is_reconciled: bool = False
    match_status: str = "UNMATCHED"
    match_confidence: Decimal | None = None
    invoice_public_id: PublicId | None = None
    invoice_number: str | None = None
    payment_public_id: PublicId | None = None
    imported_at: dt.datetime | None = None


# =============================================================================
# reconciliation
# =============================================================================
class MatchCandidateResponse(BaseModel):
    invoice_id: PublicId
    invoice_number: str | None = None
    invoice_total: Decimal
    invoice_balance_due: Decimal
    invoice_currency: str
    invoice_status: str
    counterparty: str | None = None
    project_name: str | None = None
    due_date: dt.date | None = None
    confidence: float = Field(ge=0, le=1)
    suggestion: Literal["MATCH", "REVIEW", "IGNORE"]
    reason: str
    score_breakdown: dict[str, float] = Field(default_factory=dict)


class MatchResponse(BaseModel):
    id: str
    confidence: Decimal
    score_breakdown: dict[str, Any] = Field(default_factory=dict)
    suggestion: str
    status: str
    decided_at: dt.datetime | None = None
    decision_notes: str | None = None
    transaction_id: str
    posted_at: dt.datetime
    txn_amount: Decimal
    txn_currency: str
    description_raw: str | None = None
    merchant_name: str | None = None
    invoice_public_id: PublicId
    invoice_number: str | None = None
    balance_due: Decimal
    invoice_currency: str
    due_date: dt.date | None = None
    created_at: dt.datetime


class MatchDecisionRequest(_Strict):
    decision: Literal["ACCEPTED", "REJECTED"]
    notes: str | None = Field(default=None, max_length=2000)
    create_payment: bool = True


class MatchDecisionResponse(BaseModel):
    match_id: str
    status: str
    invoice_id: PublicId | None = None
    amount: Decimal | None = None
    payment_public_id: PublicId | None = None


class ReconciliationSummaryResponse(BaseModel):
    open_txns: int = 0
    unmatched_inbound: Decimal = Decimal("0")
    unmatched_total: Decimal = Decimal("0")
    reconciled_count: int = 0
    pending_suggestions: int = 0
