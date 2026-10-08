"""Documents: metadata, versions, download authorisation and access logging.

Files live in private storage. Nothing here returns a storage path or a public
URL; a download is authorised per request (`app.can_read_document`) and then
served as a short-lived signed URL, and every access is written to
`public.document_access_log` (rule: never expose private storage without
authorisation).
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.errors import (
    BusinessRuleViolationError,
    FileTooLargeError,
    ResourceNotFoundError,
    UnsupportedMediaTypeError,
    UploadValidationError,
)
from app.core.logging import get_logger
from app.services import audit
from app.services.lookup import resolve_scoped

logger = get_logger(__name__)

ALLOWED_CONTENT_TYPES = frozenset(
    {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "text/plain",
        "text/csv",
        "text/markdown",
        "image/png",
        "image/jpeg",
        "image/webp",
        "application/json",
        "application/zip",
    }
)

# Extensions that must never be stored, regardless of the declared content type.
BLOCKED_SUFFIXES = (
    ".exe",
    ".dll",
    ".scr",
    ".bat",
    ".cmd",
    ".com",
    ".pif",
    ".jar",
    ".ps1",
    ".sh",
    ".vbs",
    ".js",
    ".jse",
    ".wsf",
    ".msi",
    ".hta",
    ".php",
)

_MAGIC_PREFIXES: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"PK\x03\x04", "application/zip"),  # also the OOXML container
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def validate_upload(
    *, filename: str, content_type: str, size: int, max_bytes: int, first_bytes: bytes
) -> None:
    """Reject an upload before anything is written.

    Checks size, declared type and — for the types we can recognise — the leading
    bytes, so a `.exe` renamed to `.pdf` is refused on content, not on extension.
    """
    if size <= 0:
        raise UploadValidationError("The uploaded file is empty.", details={"field": "file"})
    if size > max_bytes:
        raise FileTooLargeError(
            "That file is larger than the upload limit.",
            details={"max_bytes": max_bytes, "size": size},
        )

    lowered = (filename or "").lower()
    if lowered.endswith(BLOCKED_SUFFIXES):
        raise UploadValidationError(
            "That file type cannot be uploaded.",
            details={"reason": "BLOCKED_FILE_TYPE", "filename": filename},
        )

    declared = (content_type or "").split(";")[0].strip().lower()
    if declared and declared not in ALLOWED_CONTENT_TYPES:
        raise UnsupportedMediaTypeError(
            "That file type is not accepted.",
            details={"content_type": declared, "allowed": sorted(ALLOWED_CONTENT_TYPES)},
        )

    sniffed = _sniff(first_bytes)
    if sniffed and declared and sniffed not in {declared, "application/zip"}:
        raise UploadValidationError(
            "The file contents do not match the declared type.",
            details={"reason": "CONTENT_TYPE_MISMATCH", "declared": declared, "detected": sniffed},
        )


def _sniff(head: bytes) -> str | None:
    for prefix, mime in _MAGIC_PREFIXES:
        if head.startswith(prefix):
            return mime
    return None


async def _storage() -> tuple[Any, str | None]:
    """The Supabase storage client, built lazily.

    Returns None when the storage adapter is not configured; the caller reports
    that honestly rather than pretending the file was stored.
    """
    from app.core.config import get_settings

    settings = get_settings()
    url = settings.supabase_url
    key = settings.supabase_service_role_key
    if not url or not key.get_secret_value():
        return None, None

    from supabase import create_client  # type: ignore[import-not-found]

    return create_client(url, key.get_secret_value()), settings.storage_bucket_documents


_DOC_SELECT = """
    SELECT d.id, d.public_id, d.company_id, d.owner_user_id, d.doc_type, d.title,
           d.description, d.visibility, d.current_version_id, d.version_count, d.status,
           d.is_legal_hold, d.retention_until, d.retention_policy, d.related_type,
           d.related_id, d.checksum_sha256, d.ai_processing_state, d.created_at, d.updated_at,
           u.public_id AS owner_public_id,
           NULLIF(TRIM(u.first_name || ' ' || u.last_name), '') AS owner_name,
           v.version_no, v.file_name, v.mime_type AS content_type, v.byte_size, v.storage_path
      FROM public.documents d
      LEFT JOIN public.users u ON u.id = d.owner_user_id
      LEFT JOIN public.document_versions v ON v.id = d.current_version_id
"""


def _document_from_row(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["owner_user_id"] = data.pop("owner_public_id", None)
    data.pop("owner_name", None)
    data.pop("storage_path", None)
    data.pop("id", None)
    return data


async def list_documents(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    doc_type: str | None = None,
    related_type: str | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    where = ["d.company_id = :cid", "d.deleted_at IS NULL", "app.can_read_document(d.id)"]
    params: dict[str, Any] = {"cid": company_id, "limit": limit, "offset": offset}
    if doc_type:
        where.append("d.doc_type = :doc_type")
        params["doc_type"] = doc_type
    if related_type:
        where.append("d.related_type = :related_type")
        params["related_type"] = related_type
    if search:
        where.append("(d.title ILIKE :q OR d.description ILIKE :q)")
        params["q"] = f"%{search}%"

    rows = (
        (
            await conn.execute(
                text(
                    f"""
                    {_DOC_SELECT}
                     WHERE {" AND ".join(where)}
                     ORDER BY d.created_at DESC
                     LIMIT :limit OFFSET :offset
                    """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    return [_document_from_row(r) for r in rows]


async def get_document(
    conn: AsyncConnection, *, company_id: uuid.UUID, public_id: str
) -> dict[str, Any]:
    row = (
        (
            await conn.execute(
                text(f"{_DOC_SELECT} WHERE d.public_id = :pid AND d.company_id = :cid"),
                {"pid": public_id, "cid": company_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ResourceNotFoundError("Document not found.")

    allowed = await conn.execute(
        text("SELECT app.can_read_document(CAST(:did AS uuid))"), {"did": row["id"]}
    )
    if not allowed.scalar():
        # Same 404 as a missing document: readability is not disclosed.
        raise ResourceNotFoundError("Document not found.")

    data = _document_from_row(row)
    data["versions"] = await list_versions(conn, document_id=row["id"])
    return data


async def list_versions(conn: AsyncConnection, *, document_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        (
            await conn.execute(
                text(
                    """
                    SELECT v.version_no, v.file_name, v.mime_type AS content_type, v.byte_size,
                           v.checksum_sha256, v.created_at,
                           u.public_id AS uploaded_by,
                           NULLIF(TRIM(u.first_name || ' ' || u.last_name), '') AS uploaded_by_name
                      FROM public.document_versions v
                      LEFT JOIN public.users u ON u.id = v.uploaded_by
                     WHERE v.document_id = :did
                     ORDER BY v.version_no DESC
                    """
                ),
                {"did": document_id},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def create_document(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    title: str,
    doc_type: str,
    description: str | None,
    related_type: str | None,
    related_public_id: str | None,
    visibility: str = "CONNECTIONS",
    file_name: str | None = None,
    content: bytes | None = None,
    content_type: str = "application/pdf",
    max_bytes: int = 26_214_400,
) -> dict[str, Any]:
    """Register a document, optionally with its first version already uploaded."""
    entity_id: uuid.UUID | None = None
    if related_type and related_public_id:
        from app.services.lookup import resolve_by_public

        entity_id = await resolve_by_public(conn, related_type, related_public_id, company_id)

    storage_path = None
    checksum = None
    if content is not None:
        validate_upload(
            filename=file_name or f"{title}.bin",
            content_type=content_type,
            size=len(content),
            max_bytes=max_bytes,
            first_bytes=content[:16],
        )
        checksum = hashlib.sha256(content).hexdigest()
        client, bucket = await _storage()
        if client is None or bucket is None:
            raise BusinessRuleViolationError(
                "Document storage is not configured for this environment.",
                details={"reason": "STORAGE_NOT_CONFIGURED"},
            )
        storage_path = f"{company_id}/{uuid.uuid4()}/{file_name or 'document.bin'}"
        await client.storage.from_(bucket).upload(
            storage_path,
            content,
            {"content-type": content_type, "upsert": "false"},
        )

    row = (
        (
            await conn.execute(
                text(
                    """
                    INSERT INTO public.documents
                      (company_id, owner_user_id, doc_type, title, description,
                       visibility, related_type, related_id, status, checksum_sha256)
                    VALUES
                      (:cid, :actor, :doc_type, :title, :description,
                       :visibility, :related_type, CAST(:related_id AS uuid),
                       CASE WHEN :storage_path IS NULL THEN 'READY' ELSE 'PROCESSING' END,
                       :checksum)
                    RETURNING public_id
                    """
                ),
                {
                    "cid": company_id,
                    "actor": actor_user_id,
                    "doc_type": doc_type,
                    "title": title,
                    "description": description,
                    "visibility": visibility,
                    "related_type": related_type,
                    "related_id": entity_id,
                    "storage_path": storage_path,
                    "checksum": checksum,
                },
            )
        )
        .mappings()
        .first()
    )
    document_id = await resolve_scoped(
        conn, "documents", str(row["public_id"]), company_id, columns="id"
    )["id"]

    if storage_path is not None:
        await conn.execute(
            text(
                """
                INSERT INTO public.document_versions
                  (document_id, version_no, file_name, content_type, byte_size,
                   storage_path, checksum_sha256, uploaded_by)
                VALUES (CAST(:did AS uuid), 1, :file_name, :content_type, :size,
                        :storage_path, :checksum, :actor)
                """
            ),
            {
                "did": document_id,
                "file_name": file_name or "document.bin",
                "content_type": content_type,
                "size": len(content or b""),
                "storage_path": storage_path,
                "checksum": checksum,
                "actor": actor_user_id,
            },
        )

    await audit.record(
        conn,
        action="document.created",
        resource_type="document",
        resource_id=document_id,
        resource_public_id=str(row["public_id"]),
        company_id=company_id,
        actor_user_id=actor_user_id,
        new_values={
            "title": title,
            "doc_type": doc_type,
            "related_type": related_type,
            "related_id": related_public_id,
            "bytes": len(content) if content else 0,
        },
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_document(conn, company_id=company_id, public_id=str(row["public_id"]))


async def add_version(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    file_name: str,
    content: bytes,
    content_type: str,
    max_bytes: int,
    reason: str | None = None,
) -> dict[str, Any]:
    document = await resolve_scoped(conn, "documents", public_id, company_id, lock=True)
    validate_upload(
        filename=file_name,
        content_type=content_type,
        size=len(content),
        max_bytes=max_bytes,
        first_bytes=content[:16],
    )
    checksum = hashlib.sha256(content).hexdigest()

    client, bucket = await _storage()
    if client is None or bucket is None:
        raise BusinessRuleViolationError(
            "Document storage is not configured for this environment.",
            details={"reason": "STORAGE_NOT_CONFIGURED"},
        )
    version_no = int(document["version_count"] or 0) + 1
    storage_path = f"{company_id}/{public_id}/{version_no}-{file_name}"
    await client.storage.from_(bucket).upload(
        storage_path, content, {"content-type": content_type, "upsert": "false"}
    )

    await conn.execute(
        text(
            """
            INSERT INTO public.document_versions
              (document_id, version_no, file_name, content_type, byte_size,
               storage_path, checksum_sha256, uploaded_by, change_note)
            VALUES (CAST(:did AS uuid), :version, :file_name, :content_type, :size,
                    :storage_path, :checksum, :actor, :note)
            """
        ),
        {
            "did": document["id"],
            "version": version_no,
            "file_name": file_name,
            "content_type": content_type,
            "size": len(content),
            "storage_path": storage_path,
            "checksum": checksum,
            "actor": actor_user_id,
            "note": reason,
        },
    )

    await audit.record(
        conn,
        action="document.version_added",
        resource_type="document",
        resource_id=document["id"],
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        new_values={"version_no": version_no, "bytes": len(content)},
        reason=reason,
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_document(conn, company_id=company_id, public_id=public_id)


async def download_url(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    ttl_seconds: int = 300,
) -> dict[str, Any]:
    """Authorise, log, then mint a short-lived signed URL."""
    document = await resolve_scoped(conn, "documents", public_id, company_id)
    allowed = await conn.execute(
        text("SELECT app.can_read_document(CAST(:did AS uuid))"), {"did": document["id"]}
    )
    if not allowed.scalar():
        raise ResourceNotFoundError("Document not found.")

    path = (
        (
            await conn.execute(
                text(
                    """
                    SELECT storage_path FROM public.document_versions
                     WHERE document_id = CAST(:did AS uuid)
                     ORDER BY version_no DESC LIMIT 1
                    """
                ),
                {"did": document["id"]},
            )
        )
        .mappings()
        .first()
    )
    if not path or not path["storage_path"]:
        raise BusinessRuleViolationError(
            "This document has no stored file yet.",
            details={"reason": "NO_STORED_VERSION"},
        )

    client, bucket = await _storage()
    if client is None or bucket is None:
        raise BusinessRuleViolationError(
            "Document storage is not configured for this environment.",
            details={"reason": "STORAGE_NOT_CONFIGURED"},
        )

    signed = client.storage.from_(bucket).create_signed_url(path["storage_path"], ttl_seconds)

    await conn.execute(
        text(
            """
            INSERT INTO public.document_access_log
              (document_id, user_id, action, ip_address, user_agent, request_id)
            SELECT CAST(:did AS uuid), CAST(:uid AS uuid), 'DOWNLOAD', :ip, :ua, :rid
            """
        ),
        {
            "did": document["id"],
            "uid": actor_user_id,
            "ip": ip_address,
            "ua": None,
            "rid": request_id,
        },
    )
    await audit.record(
        conn,
        action="document.downloaded",
        resource_type="document",
        resource_id=document["id"],
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        request_id=request_id,
        ip_address=ip_address,
    )
    return {
        "document_id": public_id,
        "url": signed.get("signedURL") if isinstance(signed, dict) else None,
        "expires_in_seconds": ttl_seconds,
    }


async def delete_document(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    reason: str,
) -> None:
    """Soft delete.

    `trg_documents_hold` (`app.assert_document_deletable`) rejects the update when
    the document is on legal hold or still inside its retention window, so the
    check cannot be bypassed by writing to the row from anywhere.
    """
    document = await resolve_scoped(conn, "documents", public_id, company_id, lock=True)

    if document["is_legal_hold"]:
        raise BusinessRuleViolationError(
            "This document is on legal hold and cannot be deleted.",
            details={"reason": "LEGAL_HOLD"},
        )
    retention_until = document["retention_until"]
    if retention_until is not None:
        from app.core.clock import utc_today

        if retention_until > utc_today():
            raise BusinessRuleViolationError(
                f"This document is retained until {retention_until}.",
                details={"reason": "RETENTION_BLOCK", "retention_until": str(retention_until)},
            )

    await conn.execute(
        text("UPDATE public.documents SET deleted_at = now(), status = 'ARCHIVED' WHERE id = :rid"),
        {"rid": document["id"]},
    )
    await audit.record(
        conn,
        action="document.deleted",
        resource_type="document",
        resource_id=document["id"],
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        reason=reason,
        request_id=request_id,
        ip_address=ip_address,
    )


async def access_log(
    conn: AsyncConnection, *, company_id: uuid.UUID, public_id: str, limit: int = 100
) -> list[dict[str, Any]]:
    document = await resolve_scoped(conn, "documents", public_id, company_id, columns="id")
    rows = (
        (
            await conn.execute(
                text(
                    """
                    SELECT l.access_type AS action, l.accessed_at AS created_at,
                           l.ip_address,
                           u.public_id AS user_public_id,
                           NULLIF(TRIM(u.first_name || ' ' || u.last_name), '') AS user_name
                      FROM public.document_access_log l
                      LEFT JOIN public.users u ON u.id = l.user_id
                     WHERE l.document_id = :did
                     ORDER BY l.accessed_at DESC LIMIT :limit
                    """
                ),
                {"did": document["id"], "limit": limit},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


# =============================================================================
# entity helpers used by the routers
# =============================================================================
ENTITY_TABLES = {
    "PROJECT": "projects",
    "SOW": "sows",
    "CONTRACT": "contracts",
    "INVOICE": "invoices",
    "COMPANY": "companies",
    "USER": "users",
    "TIMESHEET": "timesheets",
    "MSA": "msas",
    "PAYMENT": "payments",
}


async def list_documents_for_entity(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    entity: str,
    entity_public_id: str,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Documents attached to one entity (project, SOW, contract, invoice...)."""
    from app.core.errors import ValidationError
    from app.services.lookup import resolve_by_public

    table = ENTITY_TABLES.get(entity.upper())
    if table is None:
        raise ValidationError(
            "Unknown document entity type.",
            details={"entity": entity, "allowed": sorted(ENTITY_TABLES)},
        )

    entity_id = await resolve_by_public(conn, table, entity_public_id, company_id)
    rows = (
        (
            await conn.execute(
                text(
                    """
                    SELECT d.id, d.public_id, d.doc_type, d.title, d.status,
                           d.version_count, d.created_at,
                           v.file_name, v.mime_type AS content_type, v.byte_size
                      FROM public.documents d
                      LEFT JOIN public.document_versions v ON v.id = d.current_version_id
                     WHERE d.company_id = :cid AND d.related_type = :entity
                       AND d.related_id = CAST(:eid AS uuid) AND d.deleted_at IS NULL
                       AND app.can_read_document(d.id)
                     ORDER BY d.created_at DESC LIMIT :limit
                    """
                ),
                {
                    "cid": company_id,
                    "entity": entity.upper(),
                    "eid": entity_id,
                    "limit": limit,
                },
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]
