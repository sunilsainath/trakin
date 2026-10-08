"""WORK module contracts: assignments, timesheets, entries and leave."""

from __future__ import annotations

import datetime as dt
from datetime import time as dt_time
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PublicId = str
AssignmentStatus = Literal["PENDING", "ACTIVE", "ON_LEAVE", "COMPLETED", "TERMINATED"]
TimesheetStatus = Literal["DRAFT", "SUBMITTED", "UNDER_REVIEW", "APPROVED", "LOCKED", "REJECTED"]
LeaveStatus = Literal["DRAFT", "PENDING", "APPROVED", "REJECTED", "CANCELLED"]
LeaveType = Literal[
    "ANNUAL", "SICK", "CASUAL", "PARENTAL", "UNPAID", "BEREAVEMENT", "COMP_OFF", "OTHER"
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ============================================================================ assignments
class CreateAssignmentRequest(_Strict):
    contract_id: PublicId
    project_role_id: PublicId | None = None
    user_id: PublicId
    start_date: dt.date | None = None
    end_date: dt.date | None = None
    allocation_pct: Decimal = Field(default=Decimal("100"), gt=0, le=100)
    hourly_rate: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    role_title: str | None = Field(default=None, max_length=160)

    @model_validator(mode="after")
    def _dates(self) -> CreateAssignmentRequest:
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must not precede start_date")
        return self


class UpdateAssignmentRequest(_Strict):
    start_date: dt.date | None = None
    end_date: dt.date | None = None
    allocation_pct: Decimal | None = Field(default=None, gt=0, le=100)
    hourly_rate: Decimal | None = Field(default=None, ge=0)
    role_title: str | None = Field(default=None, max_length=160)
    status: AssignmentStatus | None = None


class EndAssignmentRequest(_Strict):
    end_date: dt.date
    reason: str = Field(..., min_length=3, max_length=500)


class AssignmentResponse(BaseModel):
    id: str
    contract_id: PublicId
    contract_title: str | None = None
    contract_status: str | None = None
    project_id: PublicId
    project_name: str | None = None
    role_id: PublicId | None = None
    role_title: str | None = None
    user_id: PublicId
    user_name: str | None = None
    role_title_override: str | None = None
    hourly_rate: Decimal | None = None
    currency: str = "USD"
    start_date: dt.date
    end_date: dt.date | None = None
    status: str
    allocation_pct: Decimal = Decimal("100")
    hours_to_date: Decimal = Decimal("0")
    source: str = "CONTRACT"
    created_at: dt.datetime
    updated_at: dt.datetime


# ============================================================================ timesheets
class CreateTimesheetRequest(_Strict):
    assignment_id: str
    role_id: PublicId | None = Field(
        default=None, description="Role ID (R...) covered by the assignment"
    )
    user_id: PublicId | None = None
    period_start: dt.date | None = None
    period_end: dt.date | None = None
    billing_frequency: Literal["WEEKLY", "BIWEEKLY", "MONTHLY", "QUARTERLY", "CUSTOM"] | None = None


class TimesheetEntryRequest(_Strict):
    entry_date: dt.date
    start_time: dt_time | None = None
    end_time: dt_time | None = None
    break_minutes: int = Field(default=0, ge=0, le=720)
    hours: Decimal = Field(default=Decimal("0"), ge=0, le=24)
    is_billable: bool = True
    work_description: str = Field(default="", max_length=2000)
    project_task: str | None = Field(default=None, max_length=500)
    source: Literal["MANUAL", "AI_IMPORT", "BULK_EDIT", "API"] = "MANUAL"

    @model_validator(mode="after")
    def _clock(self) -> TimesheetEntryRequest:
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        if self.hours == 0 and not (self.start_time and self.end_time):
            raise ValueError(
                "Provide hours, or start_time and end_time so the duration can be computed"
            )
        return self


class UpdateTimesheetEntryRequest(_Strict):
    entry_date: dt.date | None = None
    start_time: dt_time | None = None
    end_time: dt_time | None = None
    break_minutes: int | None = Field(default=None, ge=0, le=720)
    hours: Decimal | None = Field(default=None, ge=0, le=24)
    is_billable: bool | None = None
    work_description: str | None = Field(default=None, max_length=2000)
    project_task: str | None = Field(default=None, max_length=500)


class TimesheetEntryResponse(BaseModel):
    id: str
    entry_date: dt.date
    start_time: dt_time | None = None
    end_time: dt_time | None = None
    break_minutes: int = 0
    hours: Decimal
    is_billable: bool
    work_description: str = ""
    project_task: str | None = None
    rate_applied: Decimal | None = None
    amount: Decimal = Decimal("0")
    currency: str = "USD"
    source: str = "MANUAL"


class TimesheetResponse(BaseModel):
    public_id: PublicId
    user_id: PublicId
    user_name: str | None = None
    project_id: PublicId
    project_name: str | None = None
    contract_id: PublicId
    contract_title: str | None = None
    role_id: PublicId | None = None
    role_title: str | None = None
    assignment_id: str
    period_start: dt.date
    period_end: dt.date
    billing_frequency: str
    status: str
    total_hours: Decimal = Decimal("0")
    billable_hours: Decimal = Decimal("0")
    total_amount: Decimal = Decimal("0")
    currency: str = "USD"
    entry_count: int = 0
    current_step: int = 0
    editable: bool = False
    submitted_at: dt.datetime | None = None
    approved_at: dt.datetime | None = None
    locked_at: dt.datetime | None = None
    rejection_reason: str | None = None
    entries: list[TimesheetEntryResponse] = Field(default_factory=list)
    approvals: list[dict[str, Any]] = Field(default_factory=list)
    revisions: list[dict[str, Any]] = Field(default_factory=list)
    created_at: dt.datetime
    updated_at: dt.datetime


class SubmitTimesheetRequest(_Strict):
    notes: str | None = Field(default=None, max_length=2000)


class TimesheetDecisionRequest(_Strict):
    step_no: int | None = Field(default=None, ge=1)
    notes: str | None = Field(default=None, max_length=2000)


class TimesheetRevisionRequest(_Strict):
    reason: str = Field(..., min_length=3, max_length=1000)
    notes: str | None = Field(default=None, max_length=2000)


# ================================================================================ leave
class CreateLeavePolicyRequest(_Strict):
    name: str = Field(..., min_length=2, max_length=160)
    leave_type: LeaveType = "ANNUAL"
    accrual_method: Literal["MONTHLY", "ANNUAL", "PER_PERIOD", "NONE"] = "MONTHLY"
    accrual_rate: Decimal = Field(default=Decimal("0"), ge=0)
    max_balance: Decimal | None = Field(default=None, ge=0)
    carry_forward_limit: Decimal | None = Field(default=None, ge=0)
    requires_approval: bool = True
    min_notice_days: int = Field(default=0, ge=0, le=365)
    max_consecutive_days: int | None = Field(default=None, ge=1, le=365)
    allow_negative_balance: bool = False
    effective_from: dt.date | None = None
    effective_to: dt.date | None = None

    @model_validator(mode="after")
    def _dates(self) -> CreateLeavePolicyRequest:
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("effective_to must not precede effective_from")
        return self


class LeavePolicyResponse(BaseModel):
    public_id: PublicId
    name: str
    leave_type: str
    accrual_method: str
    accrual_rate: Decimal
    max_balance: Decimal | None = None
    carry_forward_limit: Decimal | None = None
    requires_approval: bool
    min_notice_days: int
    max_consecutive_days: int | None = None
    allow_negative_balance: bool
    effective_from: dt.date
    effective_to: dt.date | None = None
    is_active: bool = True


class CreateLeaveRequestRequest(_Strict):
    leave_policy_id: PublicId
    start_date: dt.date
    end_date: dt.date
    total_days: Decimal | None = Field(default=None, gt=0)
    reason: str | None = Field(default=None, max_length=2000)
    user_id: PublicId | None = None

    @model_validator(mode="after")
    def _dates(self) -> CreateLeaveRequestRequest:
        if self.end_date < self.start_date:
            raise ValueError("end_date must not precede start_date")
        return self


class LeaveRequestResponse(BaseModel):
    public_id: PublicId
    user_id: PublicId
    user_name: str | None = None
    policy_public_id: PublicId | None = None
    policy_name: str | None = None
    leave_type: str | None = None
    start_date: dt.date
    end_date: dt.date
    total_days: Decimal
    reason: str | None = None
    status: str
    approver_user_id: PublicId | None = None
    decided_at: dt.datetime | None = None
    decision_notes: str | None = None
    created_at: dt.datetime | None = None


class LeaveDecisionRequest(_Strict):
    notes: str | None = Field(default=None, max_length=2000)


class LeaveBalanceResponse(BaseModel):
    policy_public_id: PublicId
    policy_name: str
    leave_type: str
    year: int
    entitled: Decimal = Decimal("0")
    accrued: Decimal = Decimal("0")
    taken: Decimal = Decimal("0")
    pending: Decimal = Decimal("0")
    carried_over: Decimal = Decimal("0")
    available: Decimal | None = None
    expires_on: dt.date | None = None
