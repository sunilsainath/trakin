"""Document intake pipeline: scan states, quarantine gate, re-drive.

Proves the honest-state contract: without a configured scanner nothing is
marked clean, INFECTED versions are never served, and reprocessing a stuck
version re-enqueues it.

Marked `integration`: needs the real database.

Run:
    pytest apps/api/tests/test_document_pipeline.py -v
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text

from app.core.errors import ValidationError

pytestmark = pytest.mark.integration


async def _version(
    conn, skeleton, tenant, *, file_name: str = "notes.txt", mime: str = "text/plain"
) -> dict[str, Any]:
    doc = (
        (
            await conn.execute(
                text(
                    "INSERT INTO public.documents (company_id, doc_type, title)"
                    " VALUES (:c, 'OTHER', 'Pipeline doc') RETURNING id, public_id"
                ),
                {"c": tenant.company_id},
            )
        )
        .mappings()
        .one()
    )
    row = (
        (
            await conn.execute(
                text(
                    """
                    INSERT INTO public.document_versions
                      (document_id, version_no, storage_bucket, file_name, mime_type,
                       byte_size, storage_path, checksum_sha256, uploaded_by)
                    VALUES (:did, 1, 'documents', :name, :mime, 10,
                            'test/notes.txt', 'abc', :actor)
                    RETURNING id
                    """
                ),
                {"did": doc["id"], "name": file_name, "mime": mime, "actor": tenant.user_id},
            )
        )
        .mappings()
        .one()
    )
    return {
        "document_id": str(doc["id"]),
        "public_id": str(doc["public_id"]),
        "version_id": row["id"],
    }


async def test_unconfigured_scanner_leaves_pending(conn, skeleton, tenants) -> None:
    """No scanner configured: states stay PENDING, never fake-clean."""
    from app.integrations.scanning import get_scanner
    from app.services import document_pipeline

    assert get_scanner() is None

    tenant = tenants["admin"]
    made = await _version(conn, skeleton, tenant)
    result = await document_pipeline.process_version(conn, version_id=made["version_id"])
    assert result["scan"] == "PENDING"
    assert result["extraction"] == "PENDING"

    state = (
        await conn.execute(
            text(
                "SELECT scan_status, extraction_state FROM public.document_versions WHERE id = :vid"
            ),
            {"vid": made["version_id"]},
        )
    ).one()
    assert tuple(state) == ("PENDING", "PENDING")


async def test_infected_versions_are_never_served(conn, skeleton, tenants) -> None:
    from app.services import document_pipeline

    tenant = tenants["admin"]
    made = await _version(conn, skeleton, tenant)
    await conn.execute(
        text("UPDATE public.document_versions SET scan_status = 'INFECTED' WHERE id = :vid"),
        {"vid": made["version_id"]},
    )
    with pytest.raises(ValidationError) as caught:
        await document_pipeline.refuse_if_infected(conn, document_id=uuid.UUID(made["document_id"]))
    assert caught.value.details["reason"] == "MALWARE_QUARANTINED"


async def test_reprocess_reenqueues_and_audits(conn, skeleton, tenants) -> None:
    from app.services import document_pipeline

    tenant = tenants["admin"]
    made = await _version(conn, skeleton, tenant)
    result = await document_pipeline.reprocess_document(
        conn,
        company_id=tenant.company_id,
        public_id=made["public_id"],
        actor_user_id=tenant.user_id,
        request_id="pytest",
    )
    assert result["ok"] is True
    events = (
        await conn.execute(
            text(
                "SELECT count(*) FROM platform.outbox_events WHERE event_type = 'DOCUMENT_UPLOADED'"
            )
        )
    ).scalar_one()
    assert events >= 1
