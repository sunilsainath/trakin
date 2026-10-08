"""Company, membership, role and settings endpoints.

Company context arrives in `X-Company-Public-Id`. Every handler below is behind
`require_company_member` or a specific `require_permission`, so an endpoint that
touches tenant data cannot be reached without both a membership and a grant.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.api.deps import (
    RequestContext,
    require_any_permission,
    require_company_member,
    require_permission,
    require_user,
)
from app.core.logging import get_logger
from app.core.rate_limit import rate_limited
from app.schemas.common import AckResponse
from app.schemas.identity import (
    CompanyResponse,
    CreateCompanyRequest,
    MembershipResponse,
    PermissionResponse,
    RoleResponse,
)
from app.services import companies as company_service

router = APIRouter(tags=["business"])
logger = get_logger(__name__)

MemberContext = Annotated[tuple[RequestContext, AsyncConnection], Depends(require_company_member)]
MembersReadContext = Annotated[
    tuple[RequestContext, AsyncConnection],
    Depends(require_any_permission("members.read", "members.manage", "roles.read")),
]
RolesContext = Annotated[
    tuple[RequestContext, AsyncConnection],
    Depends(require_any_permission("roles.read", "roles.manage")),
]
SettingsReadContext = Annotated[
    tuple[RequestContext, AsyncConnection],
    Depends(require_any_permission("settings.read", "settings.update", "dashboard.read")),
]
UserOnlyContext = Annotated[tuple[RequestContext, AsyncConnection], Depends(require_user)]


# ------------------------------------------------------------------- companies
@router.get("/companies", response_model=list[CompanyResponse], summary="My companies")
async def list_companies(ctx_and_conn: UserOnlyContext) -> list[dict[str, Any]]:
    """Companies the caller is an active member of.

    Only the caller's own memberships are returned; the count is never a
    discovery signal for how many companies exist or how many a person belongs to.

    Each row carries the caller's own role keys and resolved permissions for that
    company, so the client can render the right navigation without a second call.
    """
    ctx, conn = ctx_and_conn
    companies = await company_service.list_my_companies(conn, ctx.user_id)

    for company in companies:
        row = (
            (
                await conn.execute(
                    text(
                        """
                    SELECT app.effective_role_keys(c.id, :user_id) AS role_keys,
                           COALESCE(
                             (SELECT array_agg(perm)
                                FROM app.my_permissions(c.id, :user_id) AS perm),
                             '{}'
                           ) AS permissions
                      FROM public.companies c
                     WHERE c.public_id = :public_id
                    """
                    ),
                    {"user_id": ctx.user_id, "public_id": company["public_id"]},
                )
            )
            .mappings()
            .first()
        )

        company["my_role_keys"] = list(row["role_keys"]) if row else []
        company["my_permissions"] = sorted(row["permissions"]) if row else []

    return companies


@router.post(
    "/companies",
    response_model=CompanyResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a company (requires a processed W-9)",
)
async def create_company(
    ctx_and_conn: UserOnlyContext, payload: CreateCompanyRequest
) -> dict[str, Any]:
    """Any authenticated user may create a company and becomes its SUPER_ADMIN.

    A W-9 must already be uploaded and scanned. OCR success is never treated as
    legal or IRS verification: `verification_state` stays UNVERIFIED until an
    external check is wired in.
    """
    ctx, conn = ctx_and_conn
    return await company_service.create_company(
        conn,
        founder_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        payload=payload.model_dump(),
    )


@router.post(
    "/companies/w9-upload",
    status_code=status.HTTP_201_CREATED,
    summary="Upload a founding W-9 before the company exists",
    dependencies=[Depends(rate_limited("upload"))],
)
async def upload_founding_w9(
    ctx_and_conn: UserOnlyContext,
    file: Annotated[UploadFile, File(description="W-9 scan (PDF/image)")],
) -> dict[str, Any]:
    """Intake for company founding. No company context: the caller has none yet.

    The file is validated, stored, and queued for scan/extract like any other
    upload; the returned document id goes into `POST /companies`, which claims
    it into the new company. Rate-limited like all uploads.
    """
    from app.core.config import get_settings
    from app.services import documents as document_service

    ctx, conn = ctx_and_conn
    content = await file.read()
    return await document_service.upload_w9_intake(
        conn,
        founder_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        file_name=file.filename or "w9.pdf",
        content=content,
        content_type=file.content_type or "application/pdf",
        max_bytes=get_settings().max_upload_bytes,
    )


@router.get(
    "/companies/w9-intake/{document_public_id}",
    response_model=dict[str, Any],
    summary="Processing state of a founding W-9",
)
async def w9_intake_status(
    ctx_and_conn: UserOnlyContext, document_public_id: str
) -> dict[str, Any]:
    """Scan/extraction stage states for the review screen.

    Owner-authorised, company-less: the company does not exist yet. Only
    stage states are returned, never file bytes. A PENDING extraction state
    is reported as pending — never as extracted data.
    """
    ctx, conn = ctx_and_conn
    return await document_service.get_w9_intake_status(
        conn, public_id=document_public_id, owner_user_id=ctx.user_id
    )


@router.get("/companies/current", response_model=CompanyResponse, summary="Active company")
async def get_current_company(
    ctx_and_conn: MemberContext,
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    data = await company_service.get_company(conn, ctx.company_id)  # type: ignore[arg-type]
    data["my_role_keys"] = list(ctx.role_keys)
    # The client uses these to hide controls it cannot use. The server still
    # re-checks every permission, so a stale list can only show a control that
    # will be refused, never grant access.
    data["my_permissions"] = sorted(ctx.permissions)
    return data


@router.patch("/companies/current", response_model=CompanyResponse, summary="Update company")
async def update_current_company(
    ctx_and_conn: Annotated[
        tuple[RequestContext, AsyncConnection], Depends(require_permission("companies.update"))
    ],
    payload: dict[str, Any],
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await company_service.update_company(
        conn,
        company_id=ctx.company_id,  # type: ignore[arg-type]
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        changes=payload,
    )


@router.get("/companies/current/settings", summary="Company policy settings")
async def get_settings(ctx_and_conn: SettingsReadContext) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    row = (
        (
            await conn.execute(
                text("SELECT settings FROM public.companies WHERE id = :id"),
                {"id": ctx.company_id},
            )
        )
        .mappings()
        .first()
    )
    return {"settings": row["settings"] if row and row["settings"] else {}}


@router.put("/companies/current/settings", summary="Update company policy settings")
async def update_settings(
    ctx_and_conn: Annotated[
        tuple[RequestContext, AsyncConnection], Depends(require_permission("settings.update"))
    ],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Invoice terms, timesheet policy, segregation of duties, reconcile thresholds.

    Business rules live here as configuration, so changing a policy is a data
    change rather than a deploy.
    """
    ctx, conn = ctx_and_conn
    merged = await company_service.update_company_settings(
        conn,
        company_id=ctx.company_id,  # type: ignore[arg-type]
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        settings=payload,
    )
    return {"settings": merged}


@router.delete("/companies/current", response_model=AckResponse, summary="Archive company")
async def archive_company(
    ctx_and_conn: Annotated[
        tuple[RequestContext, AsyncConnection], Depends(require_permission("companies.delete"))
    ],
    reason: str = Query(..., min_length=3, max_length=500),
) -> AckResponse:
    """Archive, never hard delete: contracts, invoices and payments are records."""
    ctx, conn = ctx_and_conn
    await company_service.archive_company(
        conn,
        company_id=ctx.company_id,  # type: ignore[arg-type]
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        reason=reason,
    )
    return AckResponse(ok=True, message="Company archived.", request_id=ctx.request_id)


# ------------------------------------------------------------------ membership
@router.get(
    "/companies/current/members", response_model=list[MembershipResponse], summary="List members"
)
async def list_members(ctx_and_conn: MembersReadContext) -> list[dict[str, Any]]:
    """Members with their company role.

    The internal hourly rate is included only for a caller holding
    `timesheets.read_rate`; otherwise it is null rather than hidden by the UI.
    """
    ctx, conn = ctx_and_conn
    include_rate = "timesheets.read_rate" in ctx.permissions

    rows = await company_service.list_members(
        conn,
        company_id=company_service.ensure_company(ctx.company_id),
        include_rate=include_rate,
    )
    return [
        {
            "public_id": r["public_id"],
            "user": {
                "public_id": r["user_public_id"],
                "display_name": f"{r['first_name']} {r['last_name']}".strip(),
                "headline": r.get("headline"),
                "avatar_url": r.get("avatar_url"),
            },
            "role_key": r["role_key"],
            "role_name": r["role_name"],
            "status": r["status"],
            "job_title": r.get("job_title"),
            "department": r.get("department"),
            "joined_at": r.get("joined_at") or r.get("start_date"),
            "hourly_rate": r.get("hourly_rate"),
            "currency": r.get("currency"),
        }
        for r in rows
    ]


@router.patch(
    "/companies/current/members/{member_public_id}/role",
    response_model=AckResponse,
    summary="Change a member's company role",
)
async def change_member_role(
    ctx_and_conn: Annotated[
        tuple[RequestContext, AsyncConnection], Depends(require_permission("members.manage"))
    ],
    member_public_id: str,
    payload: dict[str, str],
) -> AckResponse:
    ctx, conn = ctx_and_conn
    await company_service.change_member_role(
        conn,
        company_id=ctx.company_id,  # type: ignore[arg-type]
        member_public_id=member_public_id,
        new_role_key=payload.get("role_key", ""),
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        reason=payload.get("reason"),
    )
    return AckResponse(ok=True, message="Role updated.", request_id=ctx.request_id)


@router.delete(
    "/companies/current/members/{member_public_id}",
    response_model=AckResponse,
    summary="Deactivate a member",
)
async def deactivate_member(
    ctx_and_conn: Annotated[
        tuple[RequestContext, AsyncConnection], Depends(require_permission("members.manage"))
    ],
    member_public_id: str,
    reason: str = Query(..., min_length=3, max_length=500),
) -> AckResponse:
    """Deactivate rather than delete, so the member's history survives."""
    ctx, conn = ctx_and_conn
    await company_service.deactivate_member(
        conn,
        company_id=ctx.company_id,  # type: ignore[arg-type]
        member_public_id=member_public_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        reason=reason,
    )
    return AckResponse(ok=True, message="Member deactivated.", request_id=ctx.request_id)


@router.post(
    "/companies/current/invitations",
    response_model=dict[str, Any],
    status_code=status.HTTP_201_CREATED,
    summary="Invite a user to the company",
)
async def invite_member(
    ctx_and_conn: Annotated[
        tuple[RequestContext, AsyncConnection], Depends(require_permission("members.invite"))
    ],
    payload: dict[str, str],
) -> dict[str, Any]:
    """The invitation token is returned once. Only its hash is stored."""
    ctx, conn = ctx_and_conn
    return await company_service.invite_member(
        conn,
        company_id=ctx.company_id,  # type: ignore[arg-type]
        email=payload.get("email", ""),
        role_key=payload.get("role_key", "EMPLOYEE"),
        job_title=payload.get("job_title"),
        message=payload.get("message"),
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
    )


@router.get(
    "/companies/invitations/{token}",
    response_model=dict[str, Any],
    summary="Preview a company invitation",
    dependencies=[Depends(rate_limited("invitations"))],
)
async def preview_invitation(ctx_and_conn: UserOnlyContext, token: str) -> dict[str, Any]:
    """What the invitation offers, for the accept screen.

    Authenticated: the invitee signs in first, then opens the link. The token
    itself authorises the preview; the session only proves who is asking, so
    the screen can warn when the signed-in email differs from the invited one.
    """
    _ctx, conn = ctx_and_conn
    return await company_service.preview_invitation(conn, token=token)


@router.post(
    "/companies/invitations/accept",
    response_model=dict[str, Any],
    summary="Accept a company invitation",
    dependencies=[Depends(rate_limited("invitations"))],
)
async def accept_invitation(
    ctx_and_conn: UserOnlyContext, payload: dict[str, str]
) -> dict[str, Any]:
    """Redeem an invitation token as the signed-in user.

    No company context: the invitee may belong to no company yet. The token
    must have been issued to the caller's own email address, and a used or
    expired token is rejected rather than reused.
    """
    ctx, conn = ctx_and_conn
    return await company_service.accept_invitation(
        conn,
        token=(payload.get("token") or "").strip(),
        user_id=ctx.user_id,
        request_id=ctx.request_id,
    )


# ----------------------------------------------------------------------- roles
@router.get(
    "/companies/current/roles", response_model=list[RoleResponse], summary="List company roles"
)
async def list_roles(ctx_and_conn: RolesContext) -> list[dict[str, Any]]:
    ctx, conn = ctx_and_conn
    return await company_service.list_roles(conn, ctx.company_id)  # type: ignore[arg-type]


@router.post(
    "/companies/current/roles",
    response_model=dict[str, Any],
    status_code=status.HTTP_201_CREATED,
    summary="Create a company role",
)
async def create_role(
    ctx_and_conn: Annotated[
        tuple[RequestContext, AsyncConnection], Depends(require_permission("roles.manage"))
    ],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Compose a role from the permission catalogue; no deploy required."""
    ctx, conn = ctx_and_conn
    return await company_service.create_role(
        conn,
        company_id=ctx.company_id,  # type: ignore[arg-type]
        name=payload.get("name", ""),
        description=payload.get("description", ""),
        permission_keys=list(payload.get("permissions", [])),
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
    )


@router.put(
    "/companies/current/roles/{role_public_id}/permissions",
    response_model=AckResponse,
    summary="Replace a role's permissions",
)
async def replace_role_permissions(
    ctx_and_conn: Annotated[
        tuple[RequestContext, AsyncConnection], Depends(require_permission("roles.manage"))
    ],
    role_public_id: str,
    payload: dict[str, Any],
) -> AckResponse:
    ctx, conn = ctx_and_conn
    await company_service.update_role_permissions(
        conn,
        company_id=ctx.company_id,  # type: ignore[arg-type]
        role_public_id=role_public_id,
        permission_keys=list(payload.get("permissions", [])),
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        reason=payload.get("reason"),
    )
    return AckResponse(ok=True, message="Permissions updated.", request_id=ctx.request_id)


@router.get("/permissions", response_model=list[PermissionResponse], summary="Permission catalogue")
async def list_permissions(ctx_and_conn: UserOnlyContext) -> list[dict[str, Any]]:
    """The full catalogue, so the role editor can present every available grant.

    Readable by any authenticated user: the backend still enforces what a role
    actually grants. Hiding the catalogue would only make the UI less honest
    about what is configurable.
    """
    ctx, conn = ctx_and_conn
    rows = (
        (
            await conn.execute(
                text(
                    "SELECT key, module, action, description, sensitivity, requires_approval "
                    "FROM public.permissions ORDER BY module, action"
                )
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]
