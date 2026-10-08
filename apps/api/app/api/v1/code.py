"""CODE module endpoints: /projects, /project-roles, /sows, /contracts.

Each handler does three things and nothing else: resolve the caller's company,
gate on a permission, and delegate to the service layer. Business rules,
audit writes and derived values live in `app.services.code` /
`app.services.contracts`.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncConnection

from app.api.deps import RequestContext, company_scope, require_permission
from app.core.logging import get_logger
from app.schemas.code import (
    ContractActionRequest,
    ContractResponse,
    ContractRoleResponse,
    ContractVersionResponse,
    CreateContractRequest,
    CreateProjectRequest,
    CreateProjectRoleRequest,
    CreateSowRequest,
    ProjectDashboardResponse,
    ProjectResponse,
    ProjectRoleResponse,
    SowActionRequest,
    SowResponse,
    SowVersionResponse,
    UpdateContractRequest,
    UpdateProjectRequest,
    UpdateProjectRoleRequest,
    UpdateSowRequest,
)
from app.schemas.common import AckResponse, Page, build_page, clamp_limit, decode_cursor
from app.services import code as code_service
from app.services import contracts as contract_service

router = APIRouter(tags=["code"])
logger = get_logger(__name__)

ProjectsRead = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("projects.read"))
]
ProjectsWrite = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("projects.create"))
]
ProjectsUpdate = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("projects.update"))
]
ProjectsDelete = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("projects.delete"))
]
RolesRead = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("projects.read"))
]
RolesWrite = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("projects.manage_roles"))
]
SowsRead = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("sows.read"))
]
SowsWrite = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("sows.create"))
]
SowsUpdate = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("sows.update"))
]
SowsApprove = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("sows.approve"))
]
ContractsRead = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("contracts.read"))
]
ContractsWrite = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("contracts.create"))
]
ContractsUpdate = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("contracts.update"))
]
ContractsSend = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("contracts.send"))
]
ContractsAccept = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("contracts.accept"))
]
ContractsApprove = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("contracts.approve"))
]
ContractsTerminate = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("contracts.terminate"))
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
# projects
# =============================================================================
@router.get("/projects", response_model=Page[ProjectResponse], summary="List projects")
async def list_projects(
    ctx_and_conn: ProjectsRead,
    q: str | None = Query(None, max_length=200, description="Free-text search"),
    status_filter: str | None = Query(None, alias="status", max_length=128),
    client_company_id: str | None = Query(None, max_length=32),
    owner_user_id: str | None = Query(None, max_length=32),
    limit: int = Query(25, ge=1, le=100),
    cursor: str | None = Query(None),
) -> Page[Any]:
    ctx, conn = ctx_and_conn
    page_size = clamp_limit(limit, default=25, maximum=100)
    rows = await code_service.list_projects(
        conn,
        company_id=company_scope(ctx),
        search=q,
        status=status_filter,
        client_company_id=client_company_id,
        owner_user_id=owner_user_id,
        cursor_keys=_cursor(cursor),
        limit=page_size,
    )
    return build_page(
        rows, limit=page_size, cursor_keys=("updated_at", "public_id"), request_id=ctx.request_id
    )


@router.post(
    "/projects",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a project",
)
async def create_project(
    ctx_and_conn: ProjectsWrite, payload: CreateProjectRequest
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await code_service.create_project(
        conn,
        company_id=company_scope(ctx),
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        payload=payload.model_dump(),
    )


@router.get(
    "/projects/{project_id}",
    response_model=ProjectDashboardResponse,
    summary="Project dashboard",
)
async def project_dashboard(ctx_and_conn: ProjectsRead, project_id: str) -> dict[str, Any]:
    """Everything the project page needs: roles, team, SOWs, contracts,
    timesheets, invoices, documents, activity and AI insights."""
    ctx, conn = ctx_and_conn
    from app.services import dashboard as dashboard_service
    from app.services import documents as document_service
    from app.services import work as work_service

    project = await code_service.get_project(
        conn, company_id=company_scope(ctx), public_id=project_id
    )

    roles = await code_service.list_project_roles(
        conn, company_id=company_scope(ctx), project_public_id=project_id, limit=100, cursor_keys={}
    )
    team = await work_service.list_assignments(
        conn,
        company_id=company_scope(ctx),
        project_public_id=project_id,
        actor_user_id=ctx.user_id,
        limit=100,
        cursor_keys={},
    )
    sows = await code_service.list_sows(
        conn,
        company_id=company_scope(ctx),
        project_public_id=project_id,
        status=None,
        search=None,
        limit=50,
        cursor_keys={},
    )
    for sow in sows:
        sow["roles"] = await code_service._sow_roles(conn, uuid_from_public(sow["id"]))
    contracts = await contract_service.list_contracts(
        conn,
        company_id=company_scope(ctx),
        project_public_id=project_id,
        sow_public_id=None,
        status=None,
        search=None,
        expiring_within_days=None,
        limit=50,
        cursor_keys={},
    )
    timesheets = await work_service.list_timesheets(
        conn,
        company_id=company_scope(ctx),
        actor_user_id=ctx.user_id,
        mine=False,
        project_public_id=project_id,
        limit=25,
        cursor_keys={},
    )
    documents = await document_service.list_documents_for_entity(
        conn, company_id=company_scope(ctx), entity="PROJECT", entity_public_id=project_id, limit=25
    )
    dashboard = await dashboard_service.project_dashboard(
        conn, company_id=company_scope(ctx), project_id=project_id, permissions=ctx.permissions
    )

    dashboard["project"] = project
    dashboard["roles"] = roles
    dashboard["team"] = team
    dashboard["sows"] = sows
    dashboard["contracts"] = contracts
    dashboard["timesheets"] = timesheets
    dashboard["documents"] = documents
    return dashboard


def uuid_from_public(value: str) -> Any:
    """The service layer keeps row ids as text in list payloads; this resolves one."""
    import uuid as _uuid

    return _uuid.UUID(str(value))


@router.patch("/projects/{project_id}", response_model=ProjectResponse, summary="Update a project")
async def update_project(
    ctx_and_conn: ProjectsUpdate, project_id: str, payload: UpdateProjectRequest
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await code_service.update_project(
        conn,
        company_id=company_scope(ctx),
        public_id=project_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        changes=payload.model_dump(exclude_unset=True),
    )


@router.delete("/projects/{project_id}", response_model=AckResponse, summary="Archive a project")
async def archive_project(
    ctx_and_conn: ProjectsDelete,
    project_id: str,
    reason: str = Query(..., min_length=3, max_length=500),
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    await code_service.archive_project(
        conn,
        company_id=company_scope(ctx),
        public_id=project_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        reason=reason,
    )
    return {"ok": True, "message": "Project archived.", "request_id": ctx.request_id}


# =============================================================================
# project roles
# =============================================================================
@router.get(
    "/project-roles", response_model=Page[ProjectRoleResponse], summary="List project roles"
)
async def list_project_roles(
    ctx_and_conn: RolesRead,
    project_id: str | None = Query(None, max_length=32),
    status_filter: str | None = Query(None, alias="status", max_length=128),
    q: str | None = Query(None, max_length=200),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None),
) -> Page[Any]:
    ctx, conn = ctx_and_conn
    page_size = clamp_limit(limit, default=50, maximum=200)
    rows = await code_service.list_project_roles(
        conn,
        company_id=company_scope(ctx),
        project_public_id=project_id,
        status=status_filter,
        search=q,
        limit=page_size,
        cursor_keys=_cursor(cursor),
    )
    return build_page(rows, limit=page_size, cursor_keys=("public_id",), request_id=ctx.request_id)


@router.post(
    "/project-roles",
    response_model=ProjectRoleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a project role",
)
async def create_project_role(
    ctx_and_conn: RolesWrite, project_id: str, payload: CreateProjectRoleRequest
) -> dict[str, Any]:
    """Creates a role and returns its `R...` Role ID."""
    ctx, conn = ctx_and_conn
    return await code_service.create_project_role(
        conn,
        company_id=company_scope(ctx),
        project_public_id=project_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        payload=payload.model_dump(),
    )


@router.get(
    "/project-roles/{role_id}", response_model=ProjectRoleResponse, summary="Get a project role"
)
async def get_project_role(ctx_and_conn: RolesRead, role_id: str) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await code_service.get_project_role(
        conn, company_id=company_scope(ctx), public_id=role_id
    )


@router.patch(
    "/project-roles/{role_id}", response_model=ProjectRoleResponse, summary="Update a project role"
)
async def update_project_role(
    ctx_and_conn: RolesWrite, role_id: str, payload: UpdateProjectRoleRequest
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await code_service.update_project_role(
        conn,
        company_id=company_scope(ctx),
        public_id=role_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        changes=payload.model_dump(exclude_unset=True),
    )


@router.post(
    "/project-roles/{role_id}/duplicate",
    response_model=ProjectRoleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Duplicate a project role",
)
async def duplicate_project_role(ctx_and_conn: RolesWrite, role_id: str) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await code_service.duplicate_project_role(
        conn,
        company_id=company_scope(ctx),
        public_id=role_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
    )


@router.delete(
    "/project-roles/{role_id}", response_model=AckResponse, summary="Deactivate a project role"
)
async def deactivate_project_role(
    ctx_and_conn: RolesWrite,
    role_id: str,
    reason: str = Query(..., min_length=3, max_length=500),
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    await code_service.deactivate_project_role(
        conn,
        company_id=company_scope(ctx),
        public_id=role_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        reason=reason,
    )
    return {"ok": True, "message": "Role deactivated.", "request_id": ctx.request_id}


# =============================================================================
# SOWs
# =============================================================================
@router.get("/sows", response_model=Page[SowResponse], summary="List SOWs")
async def list_sows(
    ctx_and_conn: SowsRead,
    project_id: str | None = Query(None, max_length=32),
    status_filter: str | None = Query(None, alias="status", max_length=128),
    q: str | None = Query(None, max_length=200),
    limit: int = Query(25, ge=1, le=100),
    cursor: str | None = Query(None),
) -> Page[Any]:
    ctx, conn = ctx_and_conn
    page_size = clamp_limit(limit, default=25, maximum=100)
    rows = await code_service.list_sows(
        conn,
        company_id=company_scope(ctx),
        project_public_id=project_id,
        status=status_filter,
        search=q,
        limit=page_size,
        cursor_keys=_cursor(cursor),
    )
    for sow in rows:
        sow["roles"] = await code_service._sow_roles(conn, uuid_from_public(sow["id"]))
        sow.pop("id", None)
    return build_page(
        rows, limit=page_size, cursor_keys=("updated_at", "public_id"), request_id=ctx.request_id
    )


@router.post(
    "/sows", response_model=SowResponse, status_code=status.HTTP_201_CREATED, summary="Create a SOW"
)
async def create_sow(
    ctx_and_conn: SowsWrite, project_id: str, payload: CreateSowRequest
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await code_service.create_sow(
        conn,
        company_id=company_scope(ctx),
        project_public_id=project_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        payload=payload.model_dump(),
    )


@router.get("/sows/{sow_id}", response_model=SowResponse, summary="Get a SOW")
async def get_sow(ctx_and_conn: SowsRead, sow_id: str) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await code_service.get_sow(
        conn, company_id=company_scope(ctx), public_id=sow_id, with_history=True
    )


@router.patch("/sows/{sow_id}", response_model=SowResponse, summary="Update a SOW")
async def update_sow(
    ctx_and_conn: SowsUpdate, sow_id: str, payload: UpdateSowRequest
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await code_service.update_sow(
        conn,
        company_id=company_scope(ctx),
        public_id=sow_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        changes=payload.model_dump(exclude_unset=True),
    )


@router.post("/sows/{sow_id}/submit", response_model=SowResponse, summary="Submit a SOW")
async def submit_sow(
    ctx_and_conn: SowsApprove, sow_id: str, payload: SowActionRequest | None = None
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await code_service.transition_sow(
        conn,
        company_id=company_scope(ctx),
        public_id=sow_id,
        target="PENDING_APPROVAL",
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        reason=payload.reason if payload else None,
    )


@router.post("/sows/{sow_id}/approve", response_model=SowResponse, summary="Approve a SOW")
async def approve_sow(
    ctx_and_conn: SowsApprove, sow_id: str, payload: SowActionRequest | None = None
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await code_service.transition_sow(
        conn,
        company_id=company_scope(ctx),
        public_id=sow_id,
        target="ACTIVE",
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        reason=payload.reason if payload else None,
    )


@router.post("/sows/{sow_id}/send", response_model=SowResponse, summary="Send a SOW")
async def send_sow(
    ctx_and_conn: SowsApprove, sow_id: str, payload: SowActionRequest | None = None
) -> dict[str, Any]:
    """Transmit a DRAFT SOW to its counterparty for acceptance."""
    ctx, conn = ctx_and_conn
    return await code_service.send_sow(
        conn,
        company_id=company_scope(ctx),
        public_id=sow_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        reason=payload.reason if payload else None,
    )


@router.post(
    "/sows/{sow_id}/acknowledge",
    response_model=SowResponse,
    summary="Acknowledge a received SOW",
)
async def acknowledge_sow(ctx_and_conn: SowsApprove, sow_id: str) -> dict[str, Any]:
    """Record that the counterparty has the SOW under review."""
    ctx, conn = ctx_and_conn
    return await code_service.acknowledge_sow(
        conn,
        company_id=company_scope(ctx),
        public_id=sow_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
    )


@router.post("/sows/{sow_id}/accept", response_model=SowResponse, summary="Accept a SOW")
async def accept_sow(
    ctx_and_conn: SowsApprove, sow_id: str, payload: SowActionRequest | None = None
) -> dict[str, Any]:
    """Counterparty acceptance: activates the SOW and generates its contracts."""
    ctx, conn = ctx_and_conn
    return await code_service.respond_to_sow(
        conn,
        company_id=company_scope(ctx),
        public_id=sow_id,
        accept=True,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        notes=payload.reason if payload else None,
    )


@router.post("/sows/{sow_id}/decline", response_model=SowResponse, summary="Decline a SOW")
async def decline_sow(
    ctx_and_conn: SowsApprove, sow_id: str, payload: SowActionRequest | None = None
) -> dict[str, Any]:
    """Counterparty rejection with the reason recorded; the SOW survives."""
    ctx, conn = ctx_and_conn
    return await code_service.respond_to_sow(
        conn,
        company_id=company_scope(ctx),
        public_id=sow_id,
        accept=False,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        notes=payload.reason if payload else None,
    )


@router.post("/sows/{sow_id}/reject", response_model=SowResponse, summary="Return a SOW to draft")
async def reject_sow(
    ctx_and_conn: SowsApprove, sow_id: str, payload: SowActionRequest
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await code_service.transition_sow(
        conn,
        company_id=company_scope(ctx),
        public_id=sow_id,
        target="DRAFT",
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        reason=payload.reason,
    )


@router.post("/sows/{sow_id}/reopen", response_model=SowResponse, summary="Reopen a declined SOW")
async def reopen_sow(ctx_and_conn: SowsApprove, sow_id: str) -> dict[str, Any]:
    """Return a REJECTED SOW to DRAFT so it can be revised and sent again."""
    ctx, conn = ctx_and_conn
    return await code_service.reopen_sow(
        conn,
        company_id=company_scope(ctx),
        public_id=sow_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
    )


@router.post("/sows/{sow_id}/terminate", response_model=SowResponse, summary="Terminate a SOW")
async def terminate_sow(
    ctx_and_conn: SowsApprove, sow_id: str, payload: SowActionRequest
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await code_service.transition_sow(
        conn,
        company_id=company_scope(ctx),
        public_id=sow_id,
        target="TERMINATED",
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        reason=payload.reason,
    )


@router.get(
    "/sows/{sow_id}/versions",
    response_model=list[SowVersionResponse],
    summary="SOW version history",
)
async def sow_versions(ctx_and_conn: SowsRead, sow_id: str) -> list[dict[str, Any]]:
    ctx, conn = ctx_and_conn
    sow = await code_service.get_sow(
        conn, company_id=company_scope(ctx), public_id=sow_id, with_history=True
    )
    history = sow.get("history") or []
    return [
        {
            "version": idx + 1,
            "status": entry.get("changes", {}).get("status", "DRAFT"),
            "changed_at": entry["at"],
            "changed_by": entry.get("actor_public_id"),
            "changed_by_name": entry.get("actor_name"),
            "snapshot": entry.get("changes", {}),
            "reason": entry.get("reason"),
        }
        for idx, entry in enumerate(reversed(history))
    ]


# =============================================================================
# contracts
# =============================================================================
@router.get("/contracts", response_model=Page[ContractResponse], summary="List contracts")
async def list_contracts(
    ctx_and_conn: ContractsRead,
    project_id: str | None = Query(None, max_length=32),
    sow_id: str | None = Query(None, max_length=32),
    status_filter: str | None = Query(None, alias="status", max_length=128),
    q: str | None = Query(None, max_length=200),
    expiring_within_days: int | None = Query(None, ge=1, le=365),
    limit: int = Query(25, ge=1, le=100),
    cursor: str | None = Query(None),
) -> Page[Any]:
    ctx, conn = ctx_and_conn
    page_size = clamp_limit(limit, default=25, maximum=100)
    rows = await contract_service.list_contracts(
        conn,
        company_id=company_scope(ctx),
        project_public_id=project_id,
        sow_public_id=sow_id,
        status=status_filter,
        search=q,
        expiring_within_days=expiring_within_days,
        limit=page_size,
        cursor_keys=_cursor(cursor),
    )
    for row in rows:
        row.pop("id", None)
    return build_page(
        rows, limit=page_size, cursor_keys=("updated_at", "public_id"), request_id=ctx.request_id
    )


@router.post(
    "/contracts",
    response_model=ContractResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a contract",
)
async def create_contract(
    ctx_and_conn: ContractsWrite, payload: CreateContractRequest
) -> dict[str, Any]:
    """Creates a contract referencing a project, its SOW, and Role IDs only."""
    ctx, conn = ctx_and_conn
    return await contract_service.create_contract(
        conn,
        company_id=company_scope(ctx),
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        payload=payload.model_dump(),
    )


@router.get("/contracts/{contract_id}", response_model=ContractResponse, summary="Get a contract")
async def get_contract(ctx_and_conn: ContractsRead, contract_id: str) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await contract_service.get_contract(
        conn, company_id=company_scope(ctx), public_id=contract_id
    )


@router.patch(
    "/contracts/{contract_id}", response_model=ContractResponse, summary="Update a draft contract"
)
async def update_contract(
    ctx_and_conn: ContractsUpdate, contract_id: str, payload: UpdateContractRequest
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await contract_service.update_contract(
        conn,
        company_id=company_scope(ctx),
        public_id=contract_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        changes=payload.model_dump(exclude_unset=True),
    )


@router.get(
    "/contracts/{contract_id}/roles",
    response_model=list[ContractRoleResponse],
    summary="Contract roles (authoritative billing terms)",
)
async def list_contract_roles(
    ctx_and_conn: ContractsRead, contract_id: str
) -> list[dict[str, Any]]:
    ctx, conn = ctx_and_conn
    return await contract_service.contract_roles(
        conn, company_id=company_scope(ctx), public_id=contract_id
    )


@router.post(
    "/contracts/{contract_id}/submit",
    response_model=ContractResponse,
    summary="Send a draft for internal review",
)
async def submit_contract(
    ctx_and_conn: ContractsSend, contract_id: str, payload: ContractActionRequest | None = None
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await contract_service.submit_for_review(
        conn,
        company_id=company_scope(ctx),
        public_id=contract_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        notes=payload.notes if payload else None,
    )


@router.post(
    "/contracts/{contract_id}/approvals/{step_no}",
    response_model=ContractResponse,
    summary="Decide a contract approval step",
)
async def decide_contract_approval(
    ctx_and_conn: ContractsApprove,
    contract_id: str,
    step_no: int,
    decision: str = Query(..., pattern="^(APPROVED|REJECTED)$"),
    payload: ContractActionRequest | None = None,
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await contract_service.approve_step(
        conn,
        company_id=company_scope(ctx),
        public_id=contract_id,
        step_no=step_no,
        decision=decision,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        notes=payload.notes if payload else None,
    )


@router.post(
    "/contracts/{contract_id}/send", response_model=ContractResponse, summary="Send a contract"
)
async def send_contract(
    ctx_and_conn: ContractsSend, contract_id: str, payload: ContractActionRequest | None = None
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await contract_service.send_contract(
        conn,
        company_id=company_scope(ctx),
        public_id=contract_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        notes=payload.notes if payload else None,
    )


@router.post(
    "/contracts/{contract_id}/accept",
    response_model=ContractResponse,
    summary="Accept a contract",
)
async def accept_contract(
    ctx_and_conn: ContractsAccept, contract_id: str, payload: ContractActionRequest | None = None
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await contract_service.respond_to_contract(
        conn,
        company_id=company_scope(ctx),
        public_id=contract_id,
        accept=True,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        notes=payload.notes if payload else None,
    )


@router.post(
    "/contracts/{contract_id}/decline",
    response_model=ContractResponse,
    summary="Decline a contract",
)
async def decline_contract(
    ctx_and_conn: ContractsAccept, contract_id: str, payload: ContractActionRequest
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await contract_service.respond_to_contract(
        conn,
        company_id=company_scope(ctx),
        public_id=contract_id,
        accept=False,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        notes=payload.notes,
    )


@router.post(
    "/contracts/{contract_id}/activate",
    response_model=ContractResponse,
    summary="Activate an accepted contract",
)
async def activate_contract(
    ctx_and_conn: ContractsApprove, contract_id: str, payload: ContractActionRequest | None = None
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await contract_service.activate_contract(
        conn,
        company_id=company_scope(ctx),
        public_id=contract_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        notes=payload.notes if payload else None,
    )


@router.post(
    "/contracts/{contract_id}/terminate",
    response_model=ContractResponse,
    summary="Terminate a contract",
)
async def terminate_contract(
    ctx_and_conn: ContractsTerminate, contract_id: str, payload: ContractActionRequest
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await contract_service.terminate_contract(
        conn,
        company_id=company_scope(ctx),
        public_id=contract_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        reason=payload.reason or payload.notes or "Terminated by owner",
        effective_date=payload.effective_date,
    )


@router.post(
    "/contracts/{contract_id}/close",
    response_model=ContractResponse,
    summary="Close a finished contract",
)
async def close_contract(
    ctx_and_conn: ContractsTerminate, contract_id: str, payload: ContractActionRequest
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await contract_service.close_contract(
        conn,
        company_id=company_scope(ctx),
        public_id=contract_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        reason=payload.reason or "Closed by owner",
    )


class RenewContractRequest(ContractActionRequest):
    new_start_date: str
    new_end_date: str
    contract_value: float | None = None


@router.post(
    "/contracts/{contract_id}/renew", response_model=ContractResponse, summary="Renew a contract"
)
async def renew_contract(
    ctx_and_conn: ContractsUpdate, contract_id: str, payload: RenewContractRequest
) -> dict[str, Any]:
    import datetime as _dt

    ctx, conn = ctx_and_conn
    return await contract_service.renew_contract(
        conn,
        company_id=company_scope(ctx),
        public_id=contract_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        new_start_date=_dt.date.fromisoformat(payload.new_start_date),
        new_end_date=_dt.date.fromisoformat(payload.new_end_date),
        contract_value=payload.contract_value,
    )


@router.get(
    "/contracts/{contract_id}/versions",
    response_model=list[ContractVersionResponse],
    summary="Contract audit and version trail",
)
async def contract_versions(ctx_and_conn: ContractsRead, contract_id: str) -> list[dict[str, Any]]:
    ctx, conn = ctx_and_conn
    return await contract_service.contract_versions(
        conn, company_id=company_scope(ctx), public_id=contract_id
    )
