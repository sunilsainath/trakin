"""Async document intake: scan -> extract -> classify.

Each stage is honest about what ran:
  * no scanner configured  -> scan_status stays PENDING (never "clean")
  * scanner unreachable     -> ERROR (never presented as a finding or a pass)
  * INFECTED                -> download refused, document quarantined
  * no extraction backend   -> extraction_state PENDING with a reason; the
    upload itself still succeeds because storage + metadata are real

Stages run in the `process_document_version` Celery task, enqueued via the
outbox (DOCUMENT_UPLOADED) so a missing broker loses nothing: the row is the
queue. POST /documents/{id}/process re-drives a stuck version.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.errors import BusinessRuleViolationError, ResourceNotFoundError
from app.core.logging import get_logger
from app.services import audit

logger = get_logger(__name__)

TEXT_MIME_PREFIXES = ("text/",)
TEXT_MIME_EXACT = {
    "application/json",
    "application/csv",
    "text/csv",
    "application/xml",
    "text/xml",
    "application/javascript",
}


def _classify(file_name: str, mime_type: str) -> str:
    name = (file_name or "").lower()
    mime = (mime_type or "").lower()
    if name.endswith(".pdf") or mime == "application/pdf":
        return "PDF"
    if name.endswith((".doc", ".docx")) or "wordprocessingml" in mime:
        return "WORD"
    if name.endswith((".xls", ".xlsx", ".csv")) or "spreadsheet" in mime or "csv" in mime:
        return "SPREADSHEET"
    if name.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".tiff")) or mime.startswith(
        "image/"
    ):
        return "IMAGE"
    if mime.startswith(TEXT_MIME_PREFIXES) or mime in TEXT_MIME_EXACT:
        return "TEXT"
    return "OTHER"


async def enqueue_processing(
    conn: AsyncConnection, *, version_id: uuid.UUID, company_id: uuid.UUID
) -> None:
    """Durable enqueue: the outbox row is the queue, so no broker means delay, not loss."""
    await conn.execute(
        text(
            """
            INSERT INTO platform.outbox_events
              (event_type, event_version, company_id, aggregate_type,
               aggregate_id, payload, idempotency_key)
            VALUES ('DOCUMENT_UPLOADED', 1, :cid, 'document_version',
                    :vid,
                    jsonb_build_object('version_id', CAST(:vid2 AS text)),
                    :idem)
            ON CONFLICT (event_type, idempotency_key)
              WHERE idempotency_key IS NOT NULL DO NOTHING
            """
        ),
        {
            "cid": company_id,
            "vid": version_id,
            "vid2": version_id,
            "idem": f"docintake:{version_id}",
        },
    )


async def process_version(conn: AsyncConnection, *, version_id: uuid.UUID) -> dict[str, Any]:
    """Run scan -> extract -> classify for one version. Idempotent."""
    from app.integrations.scanning import get_scanner

    row = (
        (
            await conn.execute(
                text(
                    """
                    SELECT v.id, v.document_id, v.file_name, v.mime_type, v.byte_size,
                           v.storage_path, v.scan_status, v.extraction_state,
                           d.company_id
                      FROM public.document_versions v
                      JOIN public.documents d ON d.id = v.document_id
                     WHERE v.id = :vid
                    """
                ),
                {"vid": version_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ResourceNotFoundError("Document version not found.")

    # --- scan -------------------------------------------------------------
    scanner = get_scanner()
    if scanner is None:
        logger.info("scan_skipped_unconfigured", version_id=str(version_id))
    else:
        content = await _download(conn, str(row["storage_path"]), row["company_id"])
        if content is not None:
            verdict = await scanner.scan(content, filename=str(row["file_name"] or ""))
            status = "CLEAN" if verdict.clean else ("ERROR" if verdict.error else "INFECTED")
            await conn.execute(
                text(
                    """
                    UPDATE public.document_versions
                       SET scan_status = :status, scanned_at = now(),
                           scan_engine = :engine, scan_detail = :detail
                     WHERE id = :vid
                    """
                ),
                {
                    "status": status,
                    "engine": verdict.engine,
                    "detail": verdict.detail,
                    "vid": version_id,
                },
            )
            if status == "INFECTED":
                await audit.record(
                    conn,
                    action="document.quarantined",
                    resource_type="document",
                    resource_id=row["document_id"],
                    company_id=row["company_id"],
                    new_values={"version_id": str(version_id), "engine": verdict.engine},
                    request_id="pipeline",
                )
                return {"version_id": str(version_id), "scan": "INFECTED"}

    # --- extract + classify ----------------------------------------------
    classification = _classify(str(row["file_name"] or ""), str(row["mime_type"] or ""))
    extracted: str | None = None
    extraction_state = "PENDING"
    if classification == "TEXT":
        content = await _download(conn, str(row["storage_path"]), row["company_id"])
        if content is not None:
            try:
                extracted = content.decode("utf-8", "replace")[:500_000]
                extraction_state = "EXTRACTED"
            except Exception as exc:  # noqa: BLE001 - record, don't crash intake
                logger.warning("text_decode_failed", error=str(exc)[:150])
                extraction_state = "ERROR"
    # PDF/Word/images need an OCR/extraction backend (AI gateway extraction
    # when configured); until one is wired the state stays honestly PENDING.
    await conn.execute(
        text(
            """
            UPDATE public.document_versions
               SET extracted_text = COALESCE(:extracted, extracted_text),
                   extraction_state = :state,
                   ocr_confidence = CASE WHEN :extracted IS NULL THEN ocr_confidence ELSE 1.0 END
             WHERE id = :vid
            """
        ),
        {"extracted": extracted, "state": extraction_state, "vid": version_id},
    )
    await conn.execute(
        text("UPDATE public.documents SET ai_processing_state = :state WHERE id = :did"),
        {"state": extraction_state, "did": row["document_id"]},
    )
    return {
        "version_id": str(version_id),
        "scan": "PENDING" if scanner is None else "SCANNED",
        "extraction": extraction_state,
        "classification": classification,
    }


async def _download(
    conn: AsyncConnection, storage_path: str | None, company_id: uuid.UUID
) -> bytes | None:
    if not storage_path:
        return None
    from app.services.documents import _storage

    client, bucket = await _storage()
    if client is None or bucket is None:
        return None
    try:
        return bytes(await client.storage.from_(bucket).download(storage_path))
    except Exception as exc:  # noqa: BLE001 - a missing object is not fatal
        logger.warning("pipeline_download_failed", path=storage_path, error=str(exc)[:150])
        return None


async def refuse_if_infected(conn: AsyncConnection, *, document_id: uuid.UUID) -> None:
    """Download gate: INFECTED versions are never served."""
    from app.core.errors import ValidationError

    infected = (
        await conn.execute(
            text(
                """
                SELECT 1 FROM public.document_versions
                 WHERE document_id = :did AND scan_status = 'INFECTED'
                """
            ),
            {"did": document_id},
        )
    ).scalar_one_or_none()
    if infected is not None:
        raise ValidationError(
            "This document failed its security scan and cannot be downloaded.",
            details={"reason": "MALWARE_QUARANTINED"},
        )


async def reprocess_document(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
) -> dict[str, Any]:
    from app.services.lookup import resolve_scoped

    document = await resolve_scoped(conn, "documents", public_id, company_id, columns="id")
    latest = (
        (
            await conn.execute(
                text(
                    """
                    SELECT id FROM public.document_versions
                     WHERE document_id = :did
                     ORDER BY version_no DESC LIMIT 1
                    """
                ),
                {"did": document["id"]},
            )
        )
        .mappings()
        .first()
    )
    if latest is None:
        raise BusinessRuleViolationError(
            "There is no uploaded file to process.",
            details={"reason": "NO_VERSION"},
        )
    await enqueue_processing(conn, version_id=latest["id"], company_id=company_id)
    await audit.record(
        conn,
        action="document.reprocess_requested",
        resource_type="document",
        resource_id=document["id"],
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        request_id=request_id,
    )
    return {"ok": True, "request_id": request_id}
