"""Domain and cross-cutting error types.

Every error carries a stable machine-readable `code`, an HTTP status, and a
message safe to show a user. Stack traces never cross the API boundary; they are
logged with the request id and surfaced to the caller only as that id.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for every error the API raises deliberately."""

    code: str = "INTERNAL_ERROR"
    status_code: int = 500
    # True when the message is safe to return verbatim to a client.
    public_message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> None:
        self.message = message or self.public_message
        self.details = details or {}
        self.request_id = request_id
        super().__init__(self.message)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "error": {
                "code": self.code,
                "message": self.message,
            }
        }
        if self.request_id:
            payload["error"]["request_id"] = self.request_id
        if self.details:
            payload["error"]["details"] = self.details
        return payload


# --------------------------------------------------------------- authentication
class AuthenticationError(AppError):
    code = "AUTHENTICATION_FAILED"
    status_code = 401
    public_message = "Authentication is required."


class InvalidTokenError(AuthenticationError):
    code = "INVALID_TOKEN"


class EmailNotVerifiedError(AppError):
    code = "EMAIL_NOT_VERIFIED"
    status_code = 403
    public_message = "Please verify your email address before continuing."


class AccountDisabledError(AppError):
    code = "ACCOUNT_DISABLED"
    status_code = 403
    public_message = "This account is not currently active."


# ----------------------------------------------------------------- authorization
class PermissionDeniedError(AppError):
    code = "PERMISSION_DENIED"
    status_code = 403
    public_message = "You do not have permission to perform this action."


class CompanyContextError(AppError):
    code = "COMPANY_CONTEXT_REQUIRED"
    status_code = 400
    public_message = "A company context is required for this request."


class NotAMemberError(PermissionDeniedError):
    code = "NOT_A_COMPANY_MEMBER"
    public_message = "You are not a member of this company."


# ------------------------------------------------------------------- not found
class NotFoundError(AppError):
    code = "NOT_FOUND"
    status_code = 404
    public_message = "The requested resource could not be found."


class ResourceNotFoundError(NotFoundError):
    """A cross-tenant lookup returns this, not PermissionDeniedError.

    Returning 403 would confirm that the id exists, which is an information leak.
    """

    code = "RESOURCE_NOT_FOUND"


# -------------------------------------------------------------------- conflict
class ConflictError(AppError):
    code = "CONFLICT"
    status_code = 409
    public_message = "The request conflicts with the current state."


class InvalidStateTransitionError(ConflictError):
    code = "INVALID_STATE_TRANSITION"
    public_message = "That change is not allowed from the current state."


class SegregationOfDutiesError(PermissionDeniedError):
    code = "SEGREGATION_OF_DUTIES"
    public_message = "This action cannot be performed by the same person who initiated it."


# ------------------------------------------------------------------ validation
class ValidationError(AppError):
    code = "VALIDATION_ERROR"
    status_code = 422
    public_message = "The request contains invalid data."


class BusinessRuleViolationError(ConflictError):
    code = "BUSINESS_RULE_VIOLATION"
    public_message = "This action would violate a business rule."


class AllocationExceededError(BusinessRuleViolationError):
    code = "ALLOCATION_EXCEEDS_CAPACITY"
    public_message = "The allocation would exceed the project role's required count."


class MsaRequiredError(BusinessRuleViolationError):
    code = "MSA_REQUIRED"
    public_message = "An active Master Service Agreement is required before submission."


class ContractNotActiveError(BusinessRuleViolationError):
    code = "CONTRACT_NOT_ACTIVE"
    public_message = "Work records require an accepted or active contract."


# ------------------------------------------------------------------ idempotency
class IdempotencyKeyError(AppError):
    code = "IDEMPOTENCY_KEY_REQUIRED"
    status_code = 400
    public_message = "This endpoint requires an Idempotency-Key header."


class IdempotencyKeyReuseError(AppError):
    code = "IDEMPOTENCY_KEY_REUSED"
    status_code = 409
    public_message = "This Idempotency-Key was already used with a different request body."


# ------------------------------------------------------------------ rate limit
class RateLimitExceededError(AppError):
    code = "RATE_LIMIT_EXCEEDED"
    status_code = 429
    public_message = "Too many requests. Please slow down and try again shortly."


# ---------------------------------------------------------------- integrations
class IntegrationNotConfiguredError(AppError):
    """A dependency lacks credentials.

    The feature is genuinely disabled; the API never fabricates a success in
    order to appear healthy.
    """

    code = "INTEGRATION_NOT_CONFIGURED"
    status_code = 503
    public_message = "This capability is not available in the current environment."

    def __init__(self, integration: str, *, request_id: str | None = None) -> None:
        super().__init__(
            f"The {integration} integration is not configured for this environment.",
            details={"integration": integration},
            request_id=request_id,
        )


class UpstreamError(AppError):
    code = "UPSTREAM_ERROR"
    status_code = 502
    public_message = "An upstream service did not respond as expected."


class WebhookSignatureError(AppError):
    code = "WEBHOOK_SIGNATURE_INVALID"
    status_code = 401
    public_message = "Webhook signature verification failed."


# ----------------------------------------------------------------------- AI
class AIProviderError(UpstreamError):
    code = "AI_PROVIDER_ERROR"


class AIBudgetExceededError(AppError):
    code = "AI_BUDGET_EXCEEDED"
    status_code = 429
    public_message = "The AI usage budget for this account has been reached."


class AIRetrievalError(AppError):
    code = "AI_RETRIEVAL_FAILED"
    status_code = 503
    public_message = "The assistant could not retrieve the requested information."


class AIActionNotApprovedError(PermissionDeniedError):
    code = "AI_ACTION_NOT_APPROVED"
    public_message = "This AI action requires human approval before it can run."


# -------------------------------------------------------------------- uploads
class UploadValidationError(ValidationError):
    code = "UPLOAD_REJECTED"
    public_message = "The uploaded file was rejected."


class MalwareDetectedError(AppError):
    code = "MALWARE_DETECTED"
    status_code = 422
    public_message = "The uploaded file failed its security scan."


class FileTooLargeError(ValidationError):
    code = "FILE_TOO_LARGE"
    public_message = "The uploaded file exceeds the size limit."


class UnsupportedMediaTypeError(ValidationError):
    code = "UNSUPPORTED_MEDIA_TYPE"
    public_message = "That file type is not accepted."
