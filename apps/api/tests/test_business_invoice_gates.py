"""Invoice MSA gate: a No-MSA invoice stays in Draft.

Submission for approval is refused while msa_required is set — not merely
the later approval — so an invoice without an active MSA can never enter the
approval queue. Creating the MSA clears the flag and unblocks submission.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.errors import BusinessRuleViolationError
from app.services import invoicing

pytestmark = pytest.mark.integration


async def _valued_draft(conn, skeleton, tenants) -> dict:
    tenant = tenants["admin"]
    invoice_id = (
        await conn.execute(
            text(
                """
                INSERT INTO public.invoices
                  (direction, company_id, counterparty_company_id, contract_id,
                   period_start, period_end, issue_date, due_date, currency,
                   payment_terms_days, created_by, msa_required, msa_block_reason)
                VALUES
                  ('RECEIVABLE', :c, :cp, :contract, CURRENT_DATE - 30, CURRENT_DATE,
                   CURRENT_DATE, CURRENT_DATE + 30, 'USD', 30, :actor, true,
                   'No active MSA between the companies.')
                RETURNING id::text, public_id
                """
            ),
            {
                "c": tenant.company_id,
                "cp": tenants["worker"].company_id,
                "contract": skeleton.contract,
                "actor": tenant.user_id,
            },
        )
    ).scalar_one()
    await conn.execute(
        text(
            """
            INSERT INTO public.invoice_items
              (invoice_id, contract_id, project_id, contract_role_id, line_type,
               description, quantity, unit, unit_rate, currency)
            VALUES (CAST(:i AS uuid), CAST(:c AS uuid), CAST(:p AS uuid),
                    CAST(:cr AS uuid), 'FIXED', 'Flow line', 10, 'HOUR', :r, 'USD')
            """
        ),
        {
            "i": invoice_id,
            "c": skeleton.contract,
            "p": skeleton.project,
            "cr": skeleton.contract_role,
            "r": Decimal("100"),
        },
    )
    return {"id": invoice_id}


async def test_no_msa_invoice_cannot_leave_draft(conn, skeleton, tenants) -> None:
    tenant = tenants["admin"]
    invoice = await _valued_draft(conn, skeleton, tenants)

    with pytest.raises(BusinessRuleViolationError) as exc_info:
        await invoicing.submit_for_approval(
            conn,
            company_id=tenant.company_id,
            public_id=(
                await conn.execute(
                    text("SELECT public_id FROM public.invoices WHERE id = :i"),
                    {"i": invoice["id"]},
                )
            ).scalar_one(),
            actor_user_id=tenant.user_id,
            request_id="pytest",
            ip_address=None,
            notes=None,
        )
    assert exc_info.value.details["reason"] == "MSA_REQUIRED"

    status = (
        await conn.execute(
            text("SELECT status FROM public.invoices WHERE id = :i"), {"i": invoice["id"]}
        )
    ).scalar_one()
    assert status == "DRAFT"


async def _skeleton_contract_public_id(conn, skeleton) -> str:
    return (
        await conn.execute(
            text("SELECT public_id FROM public.contracts WHERE id = :cid"),
            {"cid": skeleton.contract},
        )
    ).scalar_one()


async def test_vendor_bill_records_payable_without_msa(conn, skeleton, tenants) -> None:
    from decimal import Decimal

    tenant = tenants["admin"]
    contract_public_id = await _skeleton_contract_public_id(conn, skeleton)

    bill = await invoicing.create_vendor_bill(
        conn,
        company_id=tenant.company_id,
        actor_user_id=tenant.user_id,
        request_id="pytest",
        ip_address=None,
        payload={
            "contract_id": contract_public_id,
            "counterparty_company_id": tenants["worker"].company_public_id,
            "currency": "USD",
            "payment_terms_days": 30,
            "notes": "Subcontractor payout",
            "items": [
                {
                    "description": "Welding crew, September",
                    "quantity": 10,
                    "unit": "HOUR",
                    "unit_rate": 100,
                    "tax_rate": 0,
                },
                {
                    "description": "Equipment rental",
                    "quantity": 1,
                    "unit": "LOT",
                    "unit_rate": 250,
                    "tax_rate": 0,
                },
            ],
        },
    )
    assert bill["direction"] == "PAYABLE"
    assert bill["status"] == "DRAFT"
    assert Decimal(str(bill["total_amount"])) == Decimal("1250")

    # PAYABLE invoices are exempt from the MSA gate: owing a vendor needs no
    # agreement between the companies.
    submitted = await invoicing.submit_for_approval(
        conn,
        company_id=tenant.company_id,
        public_id=str(bill["public_id"]),
        actor_user_id=tenant.user_id,
        request_id="pytest",
        ip_address=None,
        notes=None,
    )
    assert submitted["status"] == "PENDING"


async def test_vendor_bill_requires_a_vendor_and_items(conn, skeleton, tenants) -> None:
    from app.core.errors import ValidationError

    tenant = tenants["admin"]
    contract_public_id = await _skeleton_contract_public_id(conn, skeleton)

    with pytest.raises(ValidationError):
        await invoicing.create_vendor_bill(
            conn,
            company_id=tenant.company_id,
            actor_user_id=tenant.user_id,
            request_id="pytest",
            ip_address=None,
            payload={"contract_id": contract_public_id, "items": []},
        )
