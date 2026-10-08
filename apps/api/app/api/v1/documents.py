"""Document endpoints. Upload is multipart; everything else is JSON."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncConnection

from app.api.deps import RequestContext, company_scope, require_permission
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.rate_limit import rate_limited
from app.schemas.common import AckResponse
from app.services import documents as document_service

router = APIRouter(tags=["documents"])
logger = get_logger(__name__)

DocumentsRead = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("documents.read"))
]
DocumentsUpload = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("documents.upload"))
]
DocumentsDelete = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("documents.delete"))
]
DocumentsAudit = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("documents.read_audit"))
]


@router.get("/documents", summary="List documents")
async def list_documents(
    ctx_and_conn: DocumentsRead,
    doc_type: str | None = Query(None, max_length=64),
    related_type: str | None = Query(None, max_length=32),
    q: str | None = Query(None, max_length=200),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    rows = await document_service.list_documents(
        conn,
        company_id=company_scope(ctx),
        doc_type=doc_type,
        related_type=related_type,
        search=q,
        limit=limit,
        offset=offset,
    )
    return {"data": rows, "meta": {"limit": limit, "offset": offset}, "request_id": ctx.request_id}


@router.get(
    "/documents/for/{entity}/{public_id}",
    summary="Documents attached to one entity",
)
async def list_entity_documents(
    ctx_and_conn: DocumentsRead, entity: str, public_id: str
) -> dict[str, Any]:
    """Project, SOW, contract or invoice attachments, newest first."""
    ctx, conn = ctx_and_conn
    rows = await document_service.list_documents_for_entity(
        conn,
        company_id=company_scope(ctx),
        entity=entity,
        entity_public_id=public_id,
    )
    return {"data": rows, "request_id": ctx.request_id}


@router.post(
    "/documents",
    status_code=status.HTTP_201_CREATED,
    summary="Upload a document",
    dependencies=[Depends(rate_limited("upload"))],
)
async def upload_document(
    ctx_and_conn: DocumentsUpload,
    file: Annotated[UploadFile, File(description="The file to upload")],
    title: Annotated[str, Form(description="Human-readable title")],
    doc_type: str = Form("OTHER"),
    description: str | None = Form(None),
    related_type: str | None = Form(None),
    related_public_id: str | None = Form(None),
    visibility: str = Form("CONNECTIONS"),
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    content = await file.read()
    return await document_service.create_document(
        conn,
        company_id=company_scope(ctx),
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        title=title,
        doc_type=doc_type,
        description=description,
        related_type=related_type,
        related_public_id=related_public_id,
        visibility=visibility,
        file_name=file.filename,
        content=content,
        content_type=file.content_type or "application/octet-stream",
        max_bytes=get_settings().max_upload_bytes,
    )


@router.get("/documents/{document_id}", summary="Get a document")
async def get_document(ctx_and_conn: DocumentsRead, document_id: str) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await document_service.get_document(
        conn, company_id=company_scope(ctx), public_id=document_id
    )


@router.post(
    "/documents/{document_id}/versions",
    status_code=status.HTTP_201_CREATED,
    summary="Upload a new version",
)
async def add_version(
    ctx_and_conn: DocumentsUpload,
    document_id: str,
    file: Annotated[UploadFile, File(description="The replacement file")],
    reason: Annotated[str | None, Form(description="Why this version exists")] = None,
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    content = await file.read()
    return await document_service.add_version(
        conn,
        company_id=company_scope(ctx),
        public_id=document_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        file_name=file.filename or "document.bin",
        content=content,
        content_type=file.content_type or "application/octet-stream",
        max_bytes=get_settings().max_upload_bytes,
        reason=reason,
    )


@router.get("/documents/{document_id}/download", summary="Authorised download URL")
async def download_document(ctx_and_conn: DocumentsRead, document_id: str) -> dict[str, Any]:
    """A short-lived signed URL. Authorisation is checked per request and the
    access is written to the document access log."""
    ctx, conn = ctx_and_conn
    settings = get_settings()
    return await document_service.download_url(
        conn,
        company_id=company_scope(ctx),
        public_id=document_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        ttl_seconds=settings.signed_url_ttl_seconds,
    )


@router.delete("/documents/{document_id}", response_model=AckResponse, summary="Delete a document")
async def delete_document(
    ctx_and_conn: DocumentsDelete,
    document_id: str,
    reason: str = Query(..., min_length=3, max_length=500),
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    await document_service.delete_document(
        conn,
        company_id=company_scope(ctx),
        public_id=document_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        reason=reason,
    )
    return {"ok": True, "message": "Document deleted.", "request_id": ctx.request_id}


@router.get(
    "/documents/{document_id}/access-log", summary="Document access log", response_model=list
)
async def document_access_log(
    ctx_and_conn: DocumentsAudit, document_id: str, limit: int = Query(100, ge=1, le=500)
) -> list[dict[str, Any]]:
    ctx, conn = ctx_and_conn
    return await document_service.access_log(
        conn, company_id=company_scope(ctx), public_id=document_id, limit=limit
    )


@router.post("/documents/{document_id}/process", summary="Re-drive intake pipeline")
async def reprocess_document(ctx_and_conn: DocumentsUpload, document_id: str) -> dict[str, Any]:
    """Re-enqueue scan -> extract -> classify for the latest version.

    Intake normally runs on upload; this recovers versions stuck PENDING while
    no worker was listening. Returns immediately: the work stays async.
    """
    from app.services import document_pipeline

    ctx, conn = ctx_and_conn
    return await document_pipeline.reprocess_document(
        conn,
        company_id=company_scope(ctx),
        public_id=document_id,
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
    )
