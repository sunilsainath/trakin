"""Master Service Agreement endpoints."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncConnection

from app.api.deps import RequestContext, company_scope, require_permission
from app.core.logging import get_logger
from app.services import msas as msa_service

router = APIRouter(tags=["msas"])
logger = get_logger(__name__)

MsasRead = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("msas.read"))
]
MsasRequest = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("msas.request"))
]
MsasSubmit = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("msas.submit"))
]
MsasReview = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("msas.review"))
]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CreateMsaBody(_Body):
    counterparty_company_id: str = Field(..., min_length=4, max_length=32)
    governing_law: str | None = Field(default=None, max_length=200)
    payment_terms_days: int = Field(default=30, ge=0, le=365)
    auto_renew: bool = False
    renewal_notice_days: int | None = Field(default=None, ge=1, le=365)
    notes: str | None = Field(default=None, max_length=4000)


class CreateVersionBody(_Body):
    version_no: int | None = Field(default=None, ge=1)
    effective_date: str | None = None
    expiration_date: str | None = None
    document_version_id: str | None = None


class ReviewVersionBody(_Body):
    decision: str = Field(..., pattern="^(ACCEPTED|REJECTED)$")
    notes: str | None = Field(default=None, max_length=4000)


class TransitionBody(_Body):
    reason: str | None = Field(default=None, max_length=2000)


class RequestBody(_Body):
    message: str | None = Field(default=None, max_length=4000)
    template_key: str | None = Field(default=None, max_length=64)


class RenewBody(_Body):
    effective_date: str
    expiration_date: str


@router.get("/msas", summary="List MSAs")
async def list_msas(
    ctx_and_conn: MsasRead,
    status_filter: str | None = Query(None, alias="status", max_length=32),
    counterparty_company_id: str | None = Query(None, max_length=32),
    expiring_within_days: int | None = Query(None, ge=1, le=365),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    rows = await msa_service.list_msas(
        conn,
        company_id=company_scope(ctx),
        status=status_filter,
        counterparty_company_id=counterparty_company_id,
        expiring_within_days=expiring_within_days,
        limit=limit,
        offset=offset,
    )
    return {"data": rows, "meta": {"limit": limit, "offset": offset}, "request_id": ctx.request_id}


@router.post(
    "/msas", status_code=status.HTTP_201_CREATED, summary="Open an MSA with a counterparty"
)
async def create_msa(ctx_and_conn: MsasRequest, payload: CreateMsaBody) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await msa_service.find_or_create_msa(
        conn,
        company_id=company_scope(ctx),
        counterparty_public_id=payload.counterparty_company_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        governing_law=payload.governing_law,
        payment_terms_days=payload.payment_terms_days,
        auto_renew=payload.auto_renew,
        renewal_notice_days=payload.renewal_notice_days,
        notes=payload.notes,
    )


@router.get("/msas/{msa_id}", summary="Get an MSA")
async def get_msa(ctx_and_conn: MsasRead, msa_id: str) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await msa_service.get_msa(conn, company_id=company_scope(ctx), public_id=msa_id)


@router.post(
    "/msas/{msa_id}/versions",
    status_code=status.HTTP_201_CREATED,
    summary="Add an MSA version",
)
async def create_version(
    ctx_and_conn: MsasSubmit, msa_id: str, payload: CreateVersionBody
) -> dict[str, Any]:
    import datetime as dt

    ctx, conn = ctx_and_conn
    return await msa_service.create_version(
        conn,
        company_id=company_scope(ctx),
        public_id=msa_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        version_no=payload.version_no,
        effective_date=dt.date.fromisoformat(payload.effective_date)
        if payload.effective_date
        else None,
        expiration_date=dt.date.fromisoformat(payload.expiration_date)
        if payload.expiration_date
        else None,
        document_version_id=payload.document_version_id,
    )


@router.post(
    "/msas/{msa_id}/versions/{version_no}/review", summary="Accept or reject an MSA version"
)
async def review_version(
    ctx_and_conn: MsasReview, msa_id: str, version_no: int, payload: ReviewVersionBody
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await msa_service.review_version(
        conn,
        company_id=company_scope(ctx),
        public_id=msa_id,
        version_no=version_no,
        decision=payload.decision,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        notes=payload.notes,
    )


@router.post("/msas/{msa_id}/request", summary="Ask the counterparty for an MSA")
async def create_request(
    ctx_and_conn: MsasRequest, msa_id: str, payload: RequestBody
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await msa_service.create_request(
        conn,
        company_id=company_scope(ctx),
        public_id=msa_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        message=payload.message,
        template_key=payload.template_key,
    )


@router.post("/msas/{msa_id}/activate", summary="Activate an MSA")
async def activate_msa(
    ctx_and_conn: MsasReview, msa_id: str, payload: TransitionBody | None = None
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await msa_service.transition(
        conn,
        company_id=company_scope(ctx),
        public_id=msa_id,
        target="ACTIVE",
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        reason=payload.reason if payload else None,
    )


@router.post("/msas/{msa_id}/reject", summary="Reject an MSA")
async def reject_msa(
    ctx_and_conn: MsasReview, msa_id: str, payload: TransitionBody
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await msa_service.transition(
        conn,
        company_id=company_scope(ctx),
        public_id=msa_id,
        target="REJECTED",
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        reason=payload.reason,
    )


@router.post("/msas/{msa_id}/terminate", summary="Terminate an MSA")
async def terminate_msa(
    ctx_and_conn: MsasReview, msa_id: str, payload: TransitionBody
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await msa_service.transition(
        conn,
        company_id=company_scope(ctx),
        public_id=msa_id,
        target="TERMINATED",
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        reason=payload.reason,
    )


@router.post("/msas/{msa_id}/renew", summary="Renew an MSA")
async def renew_msa(ctx_and_conn: MsasReview, msa_id: str, payload: RenewBody) -> dict[str, Any]:
    import datetime as dt

    ctx, conn = ctx_and_conn
    return await msa_service.renew(
        conn,
        company_id=company_scope(ctx),
        public_id=msa_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        effective_date=dt.date.fromisoformat(payload.effective_date),
        expiration_date=dt.date.fromisoformat(payload.expiration_date),
    )
