"""CORE module contracts: projects, project roles, SOWs and contracts.

Request bodies are strict (`extra="forbid"`) so a typo in a commercial term is
rejected rather than silently ignored. Responses are explicit projections — no
ORM row is ever serialised wholesale.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PublicId = str

CurrencyCode = str
BillingBasis = Literal["TIMESHEET", "FIXED", "RECURRING", "USAGE", "MILESTONE"]
BillingFrequency = Literal["WEEKLY", "BIWEEKLY", "MONTHLY", "QUARTERLY", "CUSTOM"]
ProjectCategory = Literal["COMPANY", "INDIVIDUAL"]
ProjectStatus = Literal["DRAFT", "PLANNING", "ACTIVE", "ON_HOLD", "COMPLETED", "CANCELLED"]
ProjectRoleStatus = Literal["OPEN", "FILLED", "CLOSED", "ON_HOLD"]
SowStatus = Literal["DRAFT", "PENDING_APPROVAL", "ACTIVE", "EXPIRED", "TERMINATED", "CLOSED"]
ContractStatus = Literal[
    "DRAFT",
    "SENT",
    "PENDING_ACCEPTANCE",
    "ACCEPTED",
    "ACTIVE",
    "DECLINED",
    "EXPIRED",
    "TERMINATED",
    "CLOSED",
]
RateType = Literal["HOURLY", "DAILY", "FIXED", "PER_UNIT", "PERCENT"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _upper_currency(v: str | None) -> str | None:
    return v.upper() if v else v


class MoneyMixin(BaseModel):
    pass


# ============================================================================ projects
class ProjectRef(BaseModel):
    """A project as referenced from another resource."""

    public_id: PublicId
    name: str
    status: str
    client_name: str | None = None


class CreateProjectRequest(_Strict):
    name: str = Field(..., min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=8000)
    project_type: Literal["SERVICE", "LENDING", "BLUE_COLLAR"] = "SERVICE"
    category: ProjectCategory = "COMPANY"
    status: ProjectStatus = "DRAFT"
    start_date: dt.date | None = None
    estimated_end_date: dt.date | None = None
    estimated_hours: Decimal | None = Field(default=None, ge=0)
    estimated_budget: Decimal | None = Field(default=None, ge=0)
    currency: CurrencyCode = "USD"
    billing_basis: BillingBasis = "TIMESHEET"
    billing_frequency: BillingFrequency = "MONTHLY"
    payment_terms_days: int = Field(default=30, ge=0, le=365)
    owner_user_id: PublicId | None = None
    counterparty_company_id: PublicId | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("currency")
    @classmethod
    def _cur(cls, v: str) -> str:
        return v.upper()

    @model_validator(mode="after")
    def _dates(self) -> CreateProjectRequest:
        if (
            self.start_date is not None
            and self.estimated_end_date is not None
            and self.estimated_end_date < self.start_date
        ):
            raise ValueError("estimated_end_date must not precede start_date")
        return self


class UpdateProjectRequest(_Strict):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=8000)
    status: ProjectStatus | None = None
    start_date: dt.date | None = None
    estimated_end_date: dt.date | None = None
    estimated_hours: Decimal | None = Field(default=None, ge=0)
    estimated_budget: Decimal | None = Field(default=None, ge=0)
    currency: CurrencyCode | None = None
    billing_basis: BillingBasis | None = None
    billing_frequency: BillingFrequency | None = None
    payment_terms_days: int | None = Field(default=None, ge=0, le=365)
    owner_user_id: PublicId | None = None
    metadata: dict[str, Any] | None = None

    @field_validator("currency")
    @classmethod
    def _cur(cls, v: str | None) -> str | None:
        return _upper_currency(v)


class ProjectResponse(BaseModel):
    public_id: PublicId
    company_id: PublicId
    name: str
    description: str | None = None
    project_type: str
    category: str
    status: str
    start_date: dt.date | None = None
    estimated_end_date: dt.date | None = None
    estimated_hours: Decimal | None = None
    estimated_budget: Decimal | None = None
    currency: str
    billing_basis: str
    billing_frequency: str
    payment_terms_days: int
    health_score: int | None = None
    owner_user_id: PublicId | None = None
    owner_name: str | None = None
    client_name: str | None = None
    client_company_id: PublicId | None = None
    contract_value: Decimal | None = None
    invoiced_total: Decimal = Decimal("0")
    outstanding_total: Decimal = Decimal("0")
    billing_status: str = "NOT_STARTED"
    team_size: int = 0
    open_role_count: int = 0
    role_count: int = 0
    sow_count: int = 0
    contract_count: int = 0
    active_contract_count: int = 0
    timesheet_count: int = 0
    last_activity_at: dt.datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: dt.datetime
    updated_at: dt.datetime


class ProjectDashboardResponse(BaseModel):
    """Everything the project detail page needs, in one round trip."""

    project: ProjectResponse
    roles: list[ProjectRoleResponse] = Field(default_factory=list)
    team: list[dict[str, Any]] = Field(default_factory=list)
    sows: list[SowResponse] = Field(default_factory=list)
    contracts: list[ContractResponse] = Field(default_factory=list)
    timesheets: list[dict[str, Any]] = Field(default_factory=list)
    invoices: list[dict[str, Any]] = Field(default_factory=list)
    documents: list[dict[str, Any]] = Field(default_factory=list)
    activity: list[dict[str, Any]] = Field(default_factory=list)
    insights: list[dict[str, Any]] = Field(default_factory=list)
    billing: dict[str, Any] = Field(default_factory=dict)
    permissions: dict[str, bool] = Field(default_factory=dict)


# ===================================================================== project roles
class CreateProjectRoleRequest(_Strict):
    title: str = Field(..., min_length=2, max_length=160)
    description: str | None = Field(default=None, max_length=4000)
    required_count: int = Field(default=1, ge=1, le=1000)
    required_skills: list[str] = Field(default_factory=list, max_length=50)
    seniority: str | None = Field(default=None, max_length=64)
    min_hourly_rate: Decimal | None = Field(default=None, ge=0)
    max_hourly_rate: Decimal | None = Field(default=None, ge=0)
    cost_rate: Decimal | None = Field(default=None, ge=0)
    currency: CurrencyCode = "USD"
    billing_basis: BillingBasis = "TIMESHEET"
    allocation_pct: Decimal = Field(default=Decimal("100"), gt=0, le=100)
    start_date: dt.date | None = None
    end_date: dt.date | None = None
    status: ProjectRoleStatus = "OPEN"

    @field_validator("currency")
    @classmethod
    def _cur(cls, v: str) -> str:
        return v.upper()

    @model_validator(mode="after")
    def _rates(self) -> CreateProjectRoleRequest:
        if (
            self.min_hourly_rate is not None
            and self.max_hourly_rate is not None
            and self.max_hourly_rate < self.min_hourly_rate
        ):
            raise ValueError("max_hourly_rate must not be below min_hourly_rate")
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must not precede start_date")
        return self


class UpdateProjectRoleRequest(_Strict):
    title: str | None = Field(default=None, min_length=2, max_length=160)
    description: str | None = Field(default=None, max_length=4000)
    required_count: int | None = Field(default=None, ge=0, le=1000)
    required_skills: list[str] | None = Field(default=None, max_length=50)
    seniority: str | None = Field(default=None, max_length=64)
    min_hourly_rate: Decimal | None = Field(default=None, ge=0)
    max_hourly_rate: Decimal | None = Field(default=None, ge=0)
    cost_rate: Decimal | None = Field(default=None, ge=0)
    currency: CurrencyCode | None = None
    billing_basis: BillingBasis | None = None
    allocation_pct: Decimal | None = Field(default=None, gt=0, le=100)
    start_date: dt.date | None = None
    end_date: dt.date | None = None
    status: ProjectRoleStatus | None = None

    @field_validator("currency")
    @classmethod
    def _cur(cls, v: str | None) -> str | None:
        return _upper_currency(v)


class ProjectRoleResponse(BaseModel):
    public_id: PublicId
    project_id: PublicId
    project_name: str | None = None
    title: str
    description: str | None = None
    required_count: int
    allocated_count: int
    required_skills: list[str] = Field(default_factory=list)
    seniority: str | None = None
    min_hourly_rate: Decimal | None = None
    max_hourly_rate: Decimal | None = None
    cost_rate: Decimal | None = None
    currency: str = "USD"
    billing_basis: str = "TIMESHEET"
    allocation_pct: Decimal = Decimal("100")
    start_date: dt.date | None = None
    end_date: dt.date | None = None
    status: str
    contracted_count: int = 0
    active_assignments: int = 0
    approved_hours_to_date: Decimal = Decimal("0")
    utilisation_pct: Decimal = Decimal("0")
    created_at: dt.datetime
    updated_at: dt.datetime


# ============================================================================ sows
class SowRoleRequest(_Strict):
    project_role_id: PublicId = Field(..., description="Role ID (R...) of a role on this project")
    quantity: int = Field(default=1, ge=1, le=10000)
    rate: Decimal | None = Field(default=None, ge=0)
    rate_type: RateType = "HOURLY"
    currency: CurrencyCode = "USD"
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("currency")
    @classmethod
    def _cur(cls, v: str) -> str:
        return v.upper()


class CreateSowRequest(_Strict):
    title: str = Field(..., min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=20000)
    sow_type: ProjectCategory = "COMPANY"
    counterparty_company_id: PublicId | None = None
    counterparty_user_id: PublicId | None = None
    start_date: dt.date | None = None
    end_date: dt.date | None = None
    currency: CurrencyCode = "USD"
    default_rate: Decimal | None = Field(default=None, ge=0)
    billing_basis: BillingBasis = "TIMESHEET"
    billing_frequency: BillingFrequency = "MONTHLY"
    invoice_frequency: BillingFrequency = "MONTHLY"
    payment_terms_days: int = Field(default=30, ge=0, le=365)
    payment_method: str | None = Field(default=None, max_length=64)
    special_conditions: str | None = Field(default=None, max_length=20000)
    max_total_amount: Decimal | None = Field(default=None, ge=0)
    scope: str | None = Field(default=None, max_length=40000)
    deliverables: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    milestones: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    roles: list[SowRoleRequest] = Field(default_factory=list, max_length=100)
    document_id: PublicId | None = None
    auto_generate_contracts: bool = True

    @field_validator("currency")
    @classmethod
    def _cur(cls, v: str) -> str:
        return v.upper()

    @model_validator(mode="after")
    def _exclusive(self) -> CreateSowRequest:
        if self.counterparty_company_id and self.counterparty_user_id:
            raise ValueError("provide a counterparty company or a counterparty user, not both")
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must not precede start_date")
        return self


class UpdateSowRequest(_Strict):
    title: str | None = Field(default=None, min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=20000)
    start_date: dt.date | None = None
    end_date: dt.date | None = None
    currency: CurrencyCode | None = None
    default_rate: Decimal | None = Field(default=None, ge=0)
    billing_basis: BillingBasis | None = None
    billing_frequency: BillingFrequency | None = None
    invoice_frequency: BillingFrequency | None = None
    payment_terms_days: int | None = Field(default=None, ge=0, le=365)
    payment_method: str | None = Field(default=None, max_length=64)
    special_conditions: str | None = Field(default=None, max_length=20000)
    max_total_amount: Decimal | None = Field(default=None, ge=0)
    scope: str | None = Field(default=None, max_length=40000)
    deliverables: list[dict[str, Any]] | None = None
    milestones: list[dict[str, Any]] | None = None
    document_id: PublicId | None = None

    @field_validator("currency")
    @classmethod
    def _cur(cls, v: str | None) -> str | None:
        return _upper_currency(v)


class SowRoleResponse(BaseModel):
    project_role_id: PublicId
    role_title: str | None = None
    quantity: int
    rate: Decimal | None = None
    rate_type: str
    currency: str
    notes: str | None = None


class SowResponse(BaseModel):
    public_id: PublicId
    project_id: PublicId
    project_name: str | None = None
    sow_type: str
    counterparty_company_id: PublicId | None = None
    counterparty_company_name: str | None = None
    counterparty_user_id: PublicId | None = None
    counterparty_user_name: str | None = None
    title: str
    description: str | None = None
    scope: str | None = None
    deliverables: list[dict[str, Any]] = Field(default_factory=list)
    milestones: list[dict[str, Any]] = Field(default_factory=list)
    status: str
    start_date: dt.date | None = None
    end_date: dt.date | None = None
    currency: str
    default_rate: Decimal | None = None
    billing_basis: str
    billing_frequency: str
    invoice_frequency: str
    payment_terms_days: int
    payment_method: str | None = None
    special_conditions: str | None = None
    max_total_amount: Decimal | None = None
    auto_generate_contracts: bool = True
    approved_by: PublicId | None = None
    approved_at: dt.datetime | None = None
    document_id: PublicId | None = None
    roles: list[SowRoleResponse] = Field(default_factory=list)
    contract_count: int = 0
    contract_ids: list[PublicId] = Field(default_factory=list)
    history: list[dict[str, Any]] = Field(default_factory=list)
    created_at: dt.datetime
    updated_at: dt.datetime


class SowActionRequest(_Strict):
    reason: str | None = Field(default=None, max_length=2000)


class SowVersionResponse(BaseModel):
    version: int
    status: str
    changed_at: dt.datetime
    changed_by: PublicId | None = None
    changed_by_name: str | None = None
    snapshot: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None


# ======================================================================== contracts
class ContractPartyRequest(_Strict):
    party_company_id: PublicId | None = None
    party_user_id: PublicId | None = None
    party_role: Literal["PRIMARY", "SUBCONTRACTOR", "SUBMITTEE", "COUNTERPARTY", "GUARANTOR"] = (
        "PRIMARY"
    )
    signatory_name: str | None = Field(default=None, max_length=200)
    signatory_email: str | None = Field(default=None, max_length=320)

    @model_validator(mode="after")
    def _one(self) -> ContractPartyRequest:
        if not self.party_company_id and not self.party_user_id:
            raise ValueError("a contract party requires a company or a user")
        return self


class ContractRoleRequest(_Strict):
    project_role_id: PublicId = Field(..., description="Role ID (R...) from the same project")
    sow_role_id: PublicId | None = None
    quantity: int = Field(default=1, ge=1, le=10000)
    rate: Decimal | None = Field(default=None, ge=0)
    rate_type: RateType = "HOURLY"
    currency: CurrencyCode = "USD"
    billing_basis: BillingBasis | None = None
    billing_frequency: BillingFrequency | None = None
    payment_terms_days: int | None = Field(default=None, ge=0, le=365)
    max_units: Decimal | None = Field(default=None, ge=0)
    start_date: dt.date | None = None
    end_date: dt.date | None = None
    overtime_rule: Literal["NONE", "MULTIPLIER", "FLAT"] = "NONE"
    overtime_rate_multiplier: Decimal = Field(default=Decimal("1.000"), ge=1)
    tax_rule: Literal["NONE", "STANDARD", "REDUCED", "ZERO", "EXEMPT", "REVERSE_CHARGE"] = (
        "STANDARD"
    )
    tax_rate: Decimal = Field(default=Decimal("0"), ge=0)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("currency")
    @classmethod
    def _cur(cls, v: str) -> str:
        return v.upper()

    @model_validator(mode="after")
    def _dates(self) -> ContractRoleRequest:
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must not precede start_date")
        return self


class ContractLineItemRequest(_Strict):
    label: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    contract_role_id: PublicId | None = None
    line_type: Literal[
        "FIXED", "RECURRING", "USAGE", "MILESTONE", "TIMESHEET", "VARIABLE", "ADDITIONAL"
    ] = "FIXED"
    quantity: Decimal = Field(default=Decimal("1"), gt=0)
    unit: str = Field(default="HOUR", max_length=32)
    unit_rate: Decimal = Field(default=Decimal("0"), ge=0)
    currency: CurrencyCode = "USD"
    billing_basis: BillingBasis = "FIXED"
    billing_frequency: BillingFrequency = "MONTHLY"
    tax_rate: Decimal = Field(default=Decimal("0"), ge=0)
    is_taxable: bool = False
    is_additional: bool = False
    third_party_name: str | None = Field(default=None, max_length=200)
    proration_start: dt.date | None = None
    proration_end: dt.date | None = None
    cap_amount: Decimal | None = Field(default=None, ge=0)
    sort_order: int = 0

    @field_validator("currency")
    @classmethod
    def _cur(cls, v: str) -> str:
        return v.upper()


class CreateContractRequest(_Strict):
    project_id: PublicId
    sow_id: PublicId
    title: str = Field(..., min_length=2, max_length=200)
    contract_type: ProjectCategory = "COMPANY"
    counterparty_company_id: PublicId | None = None
    counterparty_user_id: PublicId | None = None
    currency: CurrencyCode = "USD"
    billing_basis: BillingBasis = "TIMESHEET"
    billing_frequency: BillingFrequency = "MONTHLY"
    payment_terms_days: int = Field(default=30, ge=0, le=365)
    start_date: dt.date | None = None
    end_date: dt.date | None = None
    contract_value: Decimal | None = Field(default=None, ge=0)
    auto_renew: bool = False
    renewal_notice_days: int | None = Field(default=None, ge=1, le=365)
    termination_notice_days: int | None = Field(default=None, ge=1, le=365)
    governing_law: str | None = Field(default=None, max_length=200)
    confidentiality_level: Literal["STANDARD", "CONFIDENTIAL", "RESTRICTED"] = "STANDARD"
    requires_timesheets: bool = True
    document_id: PublicId | None = None
    parties: list[ContractPartyRequest] = Field(default_factory=list, max_length=20)
    roles: list[ContractRoleRequest] = Field(default_factory=list, max_length=100)
    line_items: list[ContractLineItemRequest] = Field(default_factory=list, max_length=200)
    terms_snapshot: dict[str, Any] = Field(default_factory=dict)

    @field_validator("currency")
    @classmethod
    def _cur(cls, v: str) -> str:
        return v.upper()

    @model_validator(mode="after")
    def _shape(self) -> CreateContractRequest:
        if self.counterparty_company_id and self.counterparty_user_id:
            raise ValueError("provide a counterparty company or a counterparty user, not both")
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must not precede start_date")
        return self


class UpdateContractRequest(_Strict):
    title: str | None = Field(default=None, min_length=2, max_length=200)
    currency: CurrencyCode | None = None
    billing_basis: BillingBasis | None = None
    billing_frequency: BillingFrequency | None = None
    payment_terms_days: int | None = Field(default=None, ge=0, le=365)
    start_date: dt.date | None = None
    end_date: dt.date | None = None
    contract_value: Decimal | None = Field(default=None, ge=0)
    auto_renew: bool | None = None
    renewal_notice_days: int | None = Field(default=None, ge=1, le=365)
    termination_notice_days: int | None = Field(default=None, ge=1, le=365)
    governing_law: str | None = Field(default=None, max_length=200)
    confidentiality_level: Literal["STANDARD", "CONFIDENTIAL", "RESTRICTED"] | None = None
    requires_timesheets: bool | None = None
    document_id: PublicId | None = None
    roles: list[ContractRoleRequest] | None = None
    line_items: list[ContractLineItemRequest] | None = None
    reason: str | None = Field(default=None, max_length=2000)

    @field_validator("currency")
    @classmethod
    def _cur(cls, v: str | None) -> str | None:
        return _upper_currency(v)


class ContractRoleResponse(BaseModel):
    project_role_id: PublicId
    role_title: str | None = None
    quantity: int
    rate: Decimal | None = None
    rate_type: str
    currency: str
    billing_basis: str
    billing_frequency: str
    payment_terms_days: int
    max_units: Decimal | None = None
    start_date: dt.date | None = None
    end_date: dt.date | None = None
    overtime_rule: str
    overtime_rate_multiplier: Decimal
    tax_rule: str
    tax_rate: Decimal
    notes: str | None = None
    billed_units: Decimal = Decimal("0")


class ContractApprovalStepResponse(BaseModel):
    step_no: int
    name: str
    status: str
    required_permission: str | None = None
    approver_user_id: PublicId | None = None
    approver_company_id: PublicId | None = None
    decided_at: dt.datetime | None = None
    notes: str | None = None


class ContractResponse(BaseModel):
    public_id: PublicId
    project_id: PublicId
    project_name: str | None = None
    sow_id: PublicId
    sow_title: str | None = None
    title: str
    contract_type: str
    status: str
    currency: str
    billing_basis: str
    billing_frequency: str
    payment_terms_days: int
    start_date: dt.date | None = None
    end_date: dt.date | None = None
    contract_value: Decimal | None = None
    auto_renew: bool = False
    renewal_notice_days: int | None = None
    termination_notice_days: int | None = None
    notice_period_end: dt.date | None = None
    governing_law: str | None = None
    confidentiality_level: str = "STANDARD"
    requires_timesheets: bool = True
    locked: bool = False
    version: int = 1
    risk_score: int | None = None
    counterparty_company_id: PublicId | None = None
    counterparty_company_name: str | None = None
    counterparty_user_id: PublicId | None = None
    counterparty_user_name: str | None = None
    document_id: PublicId | None = None
    roles: list[ContractRoleResponse] = Field(default_factory=list)
    parties: list[dict[str, Any]] = Field(default_factory=list)
    line_items: list[dict[str, Any]] = Field(default_factory=list)
    approval_steps: list[ContractApprovalStepResponse] = Field(default_factory=list)
    invoiced_total: Decimal = Decimal("0")
    outstanding_total: Decimal = Decimal("0")
    invoice_count: int = 0
    assignment_count: int = 0
    timesheet_count: int = 0
    sent_at: dt.datetime | None = None
    responded_at: dt.datetime | None = None
    activated_at: dt.datetime | None = None
    terminated_at: dt.datetime | None = None
    response_notes: str | None = None
    allowed_transitions: list[str] = Field(default_factory=list)
    created_at: dt.datetime
    updated_at: dt.datetime


class ContractActionRequest(_Strict):
    notes: str | None = Field(default=None, max_length=4000)
    reason: str | None = Field(default=None, max_length=4000)


class ContractVersionResponse(BaseModel):
    version: int
    status: str
    changed_at: dt.datetime
    changed_by: PublicId | None = None
    changed_by_name: str | None = None
    reason: str | None = None
    snapshot: dict[str, Any] = Field(default_factory=dict)
