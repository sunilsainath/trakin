"""Company founding with W-9 identity: validation, persistence, masking.

Creating a company requires the structured W-9 identity (Line 3a
classification, Part I TIN type + last-4, Lines 5-6 address) on top of the
uploaded W-9 scan. The full TIN is never stored as structured data and never
returned; responses carry only the masked last-4.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.core.errors import BusinessRuleViolationError
from app.services import companies as company_service

pytestmark = pytest.mark.integration


async def _w9_document(conn, owner_id) -> str:
    return (
        await conn.execute(
            text(
                """
                INSERT INTO public.documents
                  (company_id, owner_user_id, doc_type, title, visibility, status,
                   checksum_sha256)
                VALUES (NULL, :owner, 'W9', 'w9.pdf', 'PRIVATE', 'PROCESSING', 'abc')
                RETURNING public_id
                """
            ),
            {"owner": owner_id},
        )
    ).scalar_one()


def _payload(w9_public_id: str) -> dict:
    return {
        "legal_name": "ABC Technologies LLC",
        "display_name": "ABC Tech",
        "dba": "ABC Tech",
        "country_code": "US",
        "address_line1": "548 Market Street",
        "city": "San Francisco",
        "region": "CA",
        "postal_code": "94107",
        "default_currency": "USD",
        "tax_classification": "LLC_S_CORP",
        "tin_type": "EIN",
        "tin_last4": "4821",
        "w9_document_public_id": w9_public_id,
    }


async def test_create_company_persists_w9_identity_masked(conn, tenants) -> None:
    admin = tenants["admin"]
    w9 = await _w9_document(conn, admin.user_id)

    company = await company_service.create_company(
        conn,
        founder_user_id=admin.user_id,
        request_id="pytest",
        ip_address=None,
        payload=_payload(w9),
    )

    assert company["tin_last4_masked"] == "••••4821"
    assert company["tax_classification"] == "LLC_S_CORP"

    stored = (
        (
            await conn.execute(
                text(
                    "SELECT tax_id_type, tax_id_last4, tax_id_encrypted, tax_classification "
                    "FROM public.companies WHERE public_id = :pid"
                ),
                {"pid": company["public_id"]},
            )
        )
        .mappings()
        .one()
    )
    assert (stored["tax_id_type"], stored["tax_id_last4"]) == ("EIN", "4821")
    assert stored["tax_id_encrypted"] is None
    assert stored["tax_classification"] == "LLC_S_CORP"


async def test_create_company_rejects_incomplete_w9_with_named_fields(conn, tenants) -> None:
    admin = tenants["admin"]
    w9 = await _w9_document(conn, admin.user_id)

    payload = _payload(w9)
    payload.update(tax_classification="", tin_last4="99", postal_code="")
    with pytest.raises(BusinessRuleViolationError) as exc_info:
        await company_service.create_company(
            conn,
            founder_user_id=admin.user_id,
            request_id="pytest",
            ip_address=None,
            payload=payload,
        )
    assert set(exc_info.value.details["fields"]) == {
        "tax_classification",
        "tin_last4",
        "postal_code",
    }


async def test_create_company_rejects_a_borrowed_w9(conn, tenants) -> None:
    from app.core.errors import ResourceNotFoundError

    admin = tenants["admin"]
    worker = tenants["worker"]
    w9 = await _w9_document(conn, worker.user_id)

    with pytest.raises(ResourceNotFoundError):
        await company_service.create_company(
            conn,
            founder_user_id=admin.user_id,
            request_id="pytest",
            ip_address=None,
            payload=_payload(w9),
        )
