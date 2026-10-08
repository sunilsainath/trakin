"""Public-facing schemas.

Responses are explicit projections. Nothing is serialised with `from_attributes`
on a whole ORM row, because that is how internal columns and sensitive fields
leak into a payload by accident.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# --------------------------------------------------------------------- identity
PublicId = str


class NotificationPreferenceItem(BaseModel):
    """One category's delivery channels. Unknown categories are rejected."""

    model_config = ConfigDict(extra="forbid")

    category: str = Field(min_length=1, max_length=32)
    in_app: bool = True
    email: bool = True
    push: bool = True


class NotificationPreferenceResponse(BaseModel):
    category: str
    in_app: bool
    email: bool
    push: bool


class UserRef(BaseModel):
    """A user as referenced elsewhere. No contact details."""

    public_id: PublicId
    display_name: str
    headline: str | None = None
    avatar_url: str | None = None


class MeResponse(BaseModel):
    public_id: PublicId
    email: str
    email_verified: bool
    first_name: str
    last_name: str
    phone_e164: str | None = None
    country_code: str | None = None
    avatar_url: str | None = None
    headline: str | None = None
    bio: str | None = None
    location_city: str | None = None
    location_country: str | None = None
    timezone: str = "UTC"
    status: str = "ACTIVE"
    onboarding_completed: bool = False
    created_at: dt.datetime
    settings: dict[str, Any] = Field(default_factory=dict)


class UpdateMeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    phone_e164: str | None = Field(default=None, max_length=20)
    headline: str | None = Field(default=None, max_length=200)
    bio: str | None = Field(default=None, max_length=5000)
    location_city: str | None = Field(default=None, max_length=100)
    location_country: str | None = Field(default=None, min_length=2, max_length=2)
    timezone: str | None = Field(default=None, max_length=64)
    default_currency: str | None = Field(default=None, min_length=3, max_length=3)
    default_visibility: Literal["PUBLIC", "CONNECTIONS", "PRIVATE"] | None = None
    years_experience: float | None = Field(default=None, ge=0, le=80)
    availability_status: str | None = Field(default=None, max_length=64)
    visa_status: (
        Literal[
            "CITIZEN",
            "PERMANENT_RESIDENT",
            "WORK_VISA",
            "STUDENT_VISA",
            "OTHER",
            "PREFER_NOT_TO_SAY",
        ]
        | None
    ) = None

    @field_validator("default_currency")
    @classmethod
    def _upper_currency(cls, v: str | None) -> str | None:
        return v.upper() if v else v

    @field_validator("location_country")
    @classmethod
    def _upper_country(cls, v: str | None) -> str | None:
        return v.upper() if v else v


class SensitiveFieldRef(BaseModel):
    """A masked sensitive value. The full value is never returned."""

    field: str
    masked: str
    verification_state: str


class UserProfileResponse(BaseModel):
    public_id: PublicId
    display_name: str
    headline: str | None = None
    bio: str | None = None
    avatar_url: str | None = None
    location_city: str | None = None
    location_country: str | None = None
    timezone: str = "UTC"
    profile_visibility: str = "PUBLIC"
    skills: list[str] = Field(default_factory=list)
    years_experience: float | None = None
    availability_status: str | None = None
    connection_state: Literal["NONE", "PENDING_SENT", "PENDING_RECEIVED", "CONNECTED"] = "NONE"
    mutual_connections: int = 0
    # Only for the profile owner; masked even then.
    sensitive: list[SensitiveFieldRef] = Field(default_factory=list)


# --------------------------------------------------------------------- company
class CompanyRef(BaseModel):
    public_id: PublicId
    display_name: str
    legal_name: str | None = None
    avatar_url: str | None = None


class CompanyResponse(CompanyRef):
    country_code: str | None = None
    status: str
    default_currency: str
    verification_state: str
    tax_classification: str | None = None
    # Masked structured TIN: the last four only, never the full identifier.
    tin_last4_masked: str | None = None
    my_role_keys: list[str] = Field(default_factory=list)
    my_permissions: list[str] = Field(default_factory=list)
    created_at: dt.datetime


class CreateCompanyRequest(BaseModel):
    """Company creation. W-9 handling happens in the document pipeline."""

    model_config = ConfigDict(extra="forbid")

    legal_name: str = Field(min_length=2, max_length=200)
    display_name: str = Field(min_length=2, max_length=200)
    dba: str | None = Field(default=None, max_length=200)
    country_code: str = Field(min_length=2, max_length=2)
    address_line1: str | None = Field(default=None, max_length=200)
    address_line2: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=100)
    region: str | None = Field(default=None, max_length=100)
    postal_code: str | None = Field(default=None, max_length=20)
    default_currency: str = Field(default="USD", min_length=3, max_length=3)
    # W-9 identity (Line 3a/3b classification, Part I TIN type + last-4).
    # Full validation with field-level errors happens in the service, which
    # is also what the review screen mirrors client-side.
    tax_classification: str | None = None
    tin_type: str | None = Field(default=None, max_length=10)
    tin_last4: str | None = Field(default=None, max_length=4)
    # Reference to the uploaded W-9 document, if already processed.
    w9_document_public_id: PublicId | None = None

    @field_validator("default_currency")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()


class MembershipResponse(BaseModel):
    user: UserRef
    public_id: PublicId
    role_key: str
    role_name: str
    status: str
    job_title: str | None = None
    department: str | None = None
    joined_at: dt.datetime | None = None
    # Never included unless the caller holds timesheets.read_rate.
    hourly_rate: str | None = None
    currency: str | None = None


class RoleResponse(BaseModel):
    public_id: PublicId
    key: str
    name: str
    description: str = ""
    is_system: bool
    is_assignable: bool
    member_count: int = 0
    permissions: list[str] = Field(default_factory=list)


class PermissionResponse(BaseModel):
    key: str
    module: str
    action: str
    description: str
    sensitivity: str
    requires_approval: bool


# ------------------------------------------------------------------ auth/session
class SessionResponse(BaseModel):
    public_id: PublicId
    device: str | None = None
    ip_address: str | None = None
    current: bool = False
    created_at: dt.datetime
    last_seen_at: dt.datetime


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=8, max_length=200)
    new_password: str = Field(min_length=12, max_length=200)


# --------------------------------------------------------------------- errors
class AckResponse(BaseModel):
    ok: Literal[True] = True
    message: str | None = None
    request_id: str | None = None


# -------------------------------------------------------------------- features
class FeatureFlagState(BaseModel):
    key: str
    enabled: bool
    config: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------- notifications
class NotificationResponse(BaseModel):
    public_id: str
    type: str
    title: str
    body: str | None = None
    resource_type: str | None = None
    resource_public_id: str | None = None
    action_url: str | None = None
    severity: str = "INFO"
    read: bool = False
    created_at: dt.datetime


class UnreadCounts(BaseModel):
    notifications: int = 0
    messages: int = 0
    approvals: int = 0


# ---------------------------------------------------------------------- search
class SearchHit(BaseModel):
    entity_type: str
    public_id: PublicId
    title: str
    subtitle: str | None = None
    rank: float | None = None


# ----------------------------------------------------------------------- audit
class AuditEntry(BaseModel):
    occurred_at: dt.datetime
    action: str
    resource_type: str
    resource_public_id: str | None = None
    actor_type: str
    actor_label: str | None = None
    changed_fields: list[str] = Field(default_factory=list)
    reason: str | None = None
    request_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------- helpers
class IdResponse(BaseModel):
    id: uuid.UUID


class PublicIdResponse(BaseModel):
    public_id: PublicId
