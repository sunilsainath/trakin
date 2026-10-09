"""WORK module endpoints: /assignments, /timesheets, /timesheet-entries,
/timesheet-approvals, /leave."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncConnection

from app.api.deps import RequestContext, company_scope, require_permission
from app.core.errors import ValidationError
from app.core.logging import get_logger
from app.schemas.common import Page, build_page, clamp_limit, decode_cursor
from app.schemas.work import (
    AssignmentResponse,
    CreateAssignmentRequest,
    CreateLeavePolicyRequest,
    CreateLeaveRequestRequest,
    CreateTimesheetRequest,
    EndAssignmentRequest,
    LeaveBalanceResponse,
    LeaveDecisionRequest,
    LeavePolicyResponse,
    LeaveRequestResponse,
    SubmitTimesheetRequest,
    TimesheetDecisionRequest,
    TimesheetEntryRequest,
    TimesheetImportConfirmRequest,
    TimesheetResponse,
    TimesheetRevisionRequest,
    UpdateAssignmentRequest,
    UpdateTimesheetEntryRequest,
)
from app.services import work as work_service

router = APIRouter(tags=["work"])
logger = get_logger(__name__)

AssignmentsRead = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("assignments.read"))
]
AssignmentsCreate = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("assignments.create"))
]
AssignmentsUpdate = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("assignments.update"))
]
TimesheetsOwn = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("timesheets.create"))
]
TimesheetsReadAny = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("timesheets.read_any"))
]
TimesheetsApprove = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("timesheets.approve"))
]
LeaveOwn = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("leave.create"))
]
LeaveApprove = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("leave.approve"))
]
LeaveReadAny = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("leave.read_any"))
]
LeaveManage = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("leave.manage"))
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
# assignments
# =============================================================================
@router.get("/assignments", response_model=Page[AssignmentResponse], summary="List assignments")
async def list_assignments(
    ctx_and_conn: AssignmentsRead,
    project_id: str | None = Query(None, max_length=32),
    contract_id: str | None = Query(None, max_length=32),
    user_id: str | None = Query(None, max_length=32),
    mine: bool = Query(False),
    status_filter: str | None = Query(None, alias="status", max_length=32),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None),
) -> Page[Any]:
    ctx, conn = ctx_and_conn
    page_size = clamp_limit(limit, default=50, maximum=200)
    rows = await work_service.list_assignments(
        conn,
        company_id=company_scope(ctx),
        project_public_id=project_id,
        contract_public_id=contract_id,
        user_public_id=user_id,
        mine=mine,
        actor_user_id=ctx.user_id,
        status=status_filter,
        limit=page_size,
        cursor_keys=_cursor(cursor),
    )
    for row in rows:
        # Assignments are addressed by internal uuid in this API (PATCH takes
        # it), so the id must be present and a string for the response model.
        row["id"] = str(row.get("id"))
    return build_page(
        rows, limit=page_size, cursor_keys=("created_at", "user_id"), request_id=ctx.request_id
    )


@router.post(
    "/assignments",
    response_model=AssignmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Assign a person to a project role",
)
async def create_assignment(
    ctx_and_conn: AssignmentsCreate, payload: CreateAssignmentRequest
) -> dict[str, Any]:
    """Assigns a person to a Role ID covered by the contract. A role the contract
    does not cover is refused rather than invented."""
    ctx, conn = ctx_and_conn
    result = await work_service.create_assignment(
        conn,
        company_id=company_scope(ctx),
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        payload=payload.model_dump(),
    )
    result["role_title_override"] = result.pop("role_title", None)
    return result


@router.patch(
    "/assignments/{assignment_id:uuid}",
    response_model=AssignmentResponse,
    summary="Update an assignment",
)
async def update_assignment(
    ctx_and_conn: AssignmentsUpdate,
    assignment_id: uuid.UUID,
    payload: UpdateAssignmentRequest,
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    result = await work_service.update_assignment(
        conn,
        company_id=company_scope(ctx),
        assignment_id=assignment_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        changes=payload.model_dump(exclude_unset=True),
    )
    result["role_title_override"] = result.pop("role_title", None)
    return result


@router.post(
    "/assignments/{assignment_id:uuid}/end",
    response_model=AssignmentResponse,
    summary="End an assignment",
)
async def end_assignment(
    ctx_and_conn: AssignmentsUpdate,
    assignment_id: uuid.UUID,
    payload: EndAssignmentRequest,
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    result = await work_service.end_assignment(
        conn,
        company_id=company_scope(ctx),
        assignment_id=assignment_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        end_date=payload.end_date,
        reason=payload.reason,
    )
    result["role_title_override"] = result.pop("role_title", None)
    return result


# =============================================================================
# timesheets
# =============================================================================
@router.get("/timesheets", response_model=Page[TimesheetResponse], summary="List timesheets")
async def list_timesheets(
    ctx_and_conn: TimesheetsReadAny,
    mine: bool = Query(True),
    project_id: str | None = Query(None, max_length=32),
    contract_id: str | None = Query(None, max_length=32),
    user_id: str | None = Query(None, max_length=32),
    status_filter: str | None = Query(None, alias="status", max_length=32),
    pending_approval: bool = Query(False),
    limit: int = Query(25, ge=1, le=100),
    cursor: str | None = Query(None),
) -> Page[Any]:
    ctx, conn = ctx_and_conn
    page_size = clamp_limit(limit, default=25, maximum=100)
    rows = await work_service.list_timesheets(
        conn,
        company_id=company_scope(ctx),
        actor_user_id=ctx.user_id,
        mine=mine,
        project_public_id=project_id,
        contract_public_id=contract_id,
        user_public_id=user_id,
        status=status_filter,
        pending_approval=pending_approval,
        limit=page_size,
        cursor_keys=_cursor(cursor),
    )
    for row in rows:
        row.pop("id", None)
    return build_page(
        rows, limit=page_size, cursor_keys=("period_start", "public_id"), request_id=ctx.request_id
    )


@router.post(
    "/timesheets",
    response_model=TimesheetResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Open a timesheet",
)
async def create_timesheet(
    ctx_and_conn: TimesheetsOwn, payload: CreateTimesheetRequest
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await work_service.create_timesheet(
        conn,
        company_id=company_scope(ctx),
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        payload=payload.model_dump(),
    )


@router.get(
    "/timesheets/{timesheet_id}", response_model=TimesheetResponse, summary="Get a timesheet"
)
async def get_timesheet(ctx_and_conn: TimesheetsReadAny, timesheet_id: str) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await work_service.get_timesheet(
        conn, company_id=company_scope(ctx), public_id=timesheet_id, actor_user_id=ctx.user_id
    )


@router.post(
    "/timesheets/{timesheet_id}/entries",
    response_model=TimesheetResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record time",
)
async def add_timesheet_entry(
    ctx_and_conn: TimesheetsOwn, timesheet_id: str, payload: TimesheetEntryRequest
) -> dict[str, Any]:
    """Records time against the timesheet's project, Role ID and contract."""
    ctx, conn = ctx_and_conn
    return await work_service.add_entry(
        conn,
        company_id=company_scope(ctx),
        public_id=timesheet_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        payload=payload.model_dump(),
    )


@router.post(
    "/timesheets/{timesheet_id}/import",
    summary="Read entries out of an uploaded timesheet file",
)
async def import_timesheet_preview(
    ctx_and_conn: TimesheetsOwn,
    timesheet_id: str,
    file: Annotated[
        UploadFile, File(description="CSV/TSV/text, or a scan when extraction is enabled")
    ],
) -> dict[str, Any]:
    """Returns a preview of the rows found; writes nothing.

    The user reviews these and calls the confirm route, so an import is never a
    silent bulk edit. CSV/TSV/text is parsed deterministically; other formats
    answer with a typed "not configured" until extraction is wired.
    """
    from app.core.config import get_settings

    ctx, conn = ctx_and_conn
    content = await file.read()
    if len(content) > get_settings().max_upload_bytes:
        raise ValidationError(
            "That file is larger than the upload limit.",
            details={"reason": "FILE_TOO_LARGE"},
        )
    return await work_service.extract_timesheet_entries(
        conn,
        company_id=company_scope(ctx),
        filename=file.filename,
        content_type=file.content_type,
        content=content,
    )


@router.post(
    "/timesheets/{timesheet_id}/import/confirm",
    response_model=TimesheetResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Confirm imported entries",
)
async def import_timesheet_confirm(
    ctx_and_conn: TimesheetsOwn,
    timesheet_id: str,
    payload: TimesheetImportConfirmRequest,
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await work_service.import_timesheet_entries(
        conn,
        company_id=company_scope(ctx),
        public_id=timesheet_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        entries=[entry.model_dump() for entry in payload.entries],
    )


@router.patch(
    "/timesheet-entries/{timesheet_id}/{entry_id:uuid}",
    response_model=TimesheetResponse,
    summary="Edit a time entry",
)
async def update_timesheet_entry(
    ctx_and_conn: TimesheetsOwn,
    timesheet_id: str,
    entry_id: uuid.UUID,
    payload: UpdateTimesheetEntryRequest,
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await work_service.update_entry(
        conn,
        company_id=company_scope(ctx),
        public_id=timesheet_id,
        entry_id=entry_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        payload=payload.model_dump(exclude_unset=True),
    )


@router.delete(
    "/timesheet-entries/{timesheet_id}/{entry_id:uuid}",
    response_model=TimesheetResponse,
    summary="Remove a time entry",
)
async def delete_timesheet_entry(
    ctx_and_conn: TimesheetsOwn,
    timesheet_id: str,
    entry_id: uuid.UUID,
    reason: str = Query("Entry removed by its owner", max_length=500),
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await work_service.delete_entry(
        conn,
        company_id=company_scope(ctx),
        public_id=timesheet_id,
        entry_id=entry_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        reason=reason,
    )


@router.post(
    "/timesheets/{timesheet_id}/submit",
    response_model=TimesheetResponse,
    summary="Submit a timesheet for approval",
)
async def submit_timesheet(
    ctx_and_conn: TimesheetsOwn,
    timesheet_id: str,
    payload: SubmitTimesheetRequest | None = None,
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await work_service.submit_timesheet(
        conn,
        company_id=company_scope(ctx),
        public_id=timesheet_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        notes=payload.notes if payload else None,
    )


@router.post(
    "/timesheet-approvals/{timesheet_id}",
    response_model=TimesheetResponse,
    summary="Approve or reject a timesheet",
)
async def decide_timesheet(
    ctx_and_conn: TimesheetsApprove,
    timesheet_id: str,
    decision: str = Query(..., pattern="^(APPROVED|REJECTED)$"),
    payload: TimesheetDecisionRequest | None = None,
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await work_service.decide_timesheet(
        conn,
        company_id=company_scope(ctx),
        public_id=timesheet_id,
        step_no=payload.step_no if payload else None,
        decision=decision,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        notes=payload.notes if payload else None,
    )


@router.post(
    "/timesheets/{timesheet_id}/lock",
    response_model=TimesheetResponse,
    summary="Lock an approved timesheet",
)
async def lock_timesheet(
    ctx_and_conn: TimesheetsApprove,
    timesheet_id: str,
    payload: TimesheetDecisionRequest | None = None,
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await work_service.lock_timesheet(
        conn,
        company_id=company_scope(ctx),
        public_id=timesheet_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        notes=payload.notes if payload else None,
    )


@router.post(
    "/timesheets/{timesheet_id}/revise",
    response_model=TimesheetResponse,
    summary="Open a controlled revision of an approved timesheet",
)
async def revise_timesheet(
    ctx_and_conn: TimesheetsApprove, timesheet_id: str, payload: TimesheetRevisionRequest
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await work_service.revise_timesheet(
        conn,
        company_id=company_scope(ctx),
        public_id=timesheet_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        reason=payload.reason,
        notes=payload.notes,
    )


# =============================================================================
# leave
# =============================================================================
@router.get("/leave/policies", response_model=list[LeavePolicyResponse], summary="Leave policies")
async def list_leave_policies(ctx_and_conn: LeaveReadAny) -> list[dict[str, Any]]:
    ctx, conn = ctx_and_conn
    return await work_service.list_leave_policies(conn, company_id=company_scope(ctx))


@router.post(
    "/leave/policies",
    response_model=LeavePolicyResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a leave policy",
)
async def create_leave_policy(
    ctx_and_conn: LeaveManage, payload: CreateLeavePolicyRequest
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await work_service.create_leave_policy(
        conn,
        company_id=company_scope(ctx),
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        payload=payload.model_dump(),
    )


@router.get("/leave", response_model=Page[LeaveRequestResponse], summary="List leave requests")
async def list_leave_requests(
    ctx_and_conn: LeaveReadAny,
    mine: bool = Query(True),
    user_id: str | None = Query(None, max_length=32),
    status_filter: str | None = Query(None, alias="status", max_length=32),
    limit: int = Query(25, ge=1, le=100),
    cursor: str | None = Query(None),
) -> Page[Any]:
    ctx, conn = ctx_and_conn
    page_size = clamp_limit(limit, default=25, maximum=100)
    rows = await work_service.list_leave_requests(
        conn,
        company_id=company_scope(ctx),
        actor_user_id=ctx.user_id,
        mine=mine,
        user_public_id=user_id,
        status=status_filter,
        limit=page_size,
        cursor_keys=_cursor(cursor),
    )
    return build_page(
        rows, limit=page_size, cursor_keys=("start_date", "public_id"), request_id=ctx.request_id
    )


@router.get(
    "/leave/balances",
    response_model=list[LeaveBalanceResponse],
    summary="Leave balances",
)
async def leave_balances(
    ctx_and_conn: LeaveReadAny,
    user_id: str | None = Query(None, max_length=32),
    year: int | None = Query(None, ge=2000, le=2200),
) -> list[dict[str, Any]]:
    ctx, conn = ctx_and_conn
    return await work_service.leave_balances(
        conn,
        company_id=company_scope(ctx),
        actor_user_id=ctx.user_id,
        user_public_id=user_id,
        year=year,
    )


@router.post(
    "/leave",
    response_model=LeaveRequestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Request leave",
)
async def create_leave_request(
    ctx_and_conn: LeaveOwn, payload: CreateLeaveRequestRequest
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await work_service.create_leave_request(
        conn,
        company_id=company_scope(ctx),
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        payload=payload.model_dump(),
    )


@router.get("/leave/{leave_id}", response_model=LeaveRequestResponse, summary="Get a leave request")
async def get_leave_request(ctx_and_conn: LeaveReadAny, leave_id: str) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await work_service.get_leave_request(
        conn, company_id=company_scope(ctx), public_id=leave_id
    )


@router.post(
    "/leave/{leave_id}/approve",
    response_model=LeaveRequestResponse,
    summary="Approve leave",
)
async def approve_leave(
    ctx_and_conn: LeaveApprove, leave_id: str, payload: LeaveDecisionRequest | None = None
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await work_service.decide_leave_request(
        conn,
        company_id=company_scope(ctx),
        public_id=leave_id,
        decision="APPROVED",
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        notes=payload.notes if payload else None,
    )


@router.post(
    "/leave/{leave_id}/reject",
    response_model=LeaveRequestResponse,
    summary="Reject leave",
)
async def reject_leave(
    ctx_and_conn: LeaveApprove, leave_id: str, payload: LeaveDecisionRequest
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await work_service.decide_leave_request(
        conn,
        company_id=company_scope(ctx),
        public_id=leave_id,
        decision="REJECTED",
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        notes=payload.notes,
    )


@router.post(
    "/leave/{leave_id}/cancel",
    response_model=LeaveRequestResponse,
    summary="Cancel a leave request",
)
async def cancel_leave(
    ctx_and_conn: LeaveOwn, leave_id: str, reason: str = Query(..., min_length=3, max_length=500)
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await work_service.cancel_leave_request(
        conn,
        company_id=company_scope(ctx),
        public_id=leave_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        reason=reason,
    )
