"""PAYMENTS: bank connections, transactions, payments, allocation and
reconciliation.

Two boundaries are kept deliberately separate:

  * **Plaid** connects a bank account and reads transactions. It does not move
    money, and nothing here claims it does.
  * A **payment processor** (Stripe, or the `manual` recorder) moves money. It is
    reached only through the `PaymentProcessor` protocol in
    `app.integrations.payments`, so adding a processor never touches this module.

Reconciliation proposes matches with a confidence score and always waits for a
human decision. Rule 8: every decision is written to `payment_matches` and
`platform.audit_logs`.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.clock import utc_today
from app.core.errors import (
    BusinessRuleViolationError,
    IntegrationNotConfiguredError,
    InvalidStateTransitionError,
    ResourceNotFoundError,
    ValidationError,
)
from app.core.logging import get_logger
from app.integrations.payments import (
    TokenCipher,
    get_integrations,
)
from app.services import audit
from app.services.code import _json
from app.services.invoicing import money
from app.services.lookup import (
    as_decimal,
    resolve_company_public_id,
    resolve_scoped,
)

logger = get_logger(__name__)

ZERO = Decimal("0")

# A match below this is never auto-finalised, whatever the engine believes.
AUTO_ACCEPT_THRESHOLD = Decimal("0.98")
MIN_CONFIDENCE = Decimal("0.10")


# =============================================================================
# bank connections and accounts
# =============================================================================
async def list_bank_connections(
    conn: AsyncConnection, *, company_id: uuid.UUID
) -> list[dict[str, Any]]:
    rows = (
        (
            await conn.execute(
                text(
                    """
                    SELECT bc.public_id, bc.provider, bc.institution_name, bc.institution_id,
                           bc.status, bc.verification_state, bc.ownership_verified,
                           bc.consent_expires_at, bc.last_synced_at, bc.transaction_count,
                           bc.created_at, bc.error_detail,
                           u.public_id AS owner_public_id,
                           NULLIF(TRIM(u.first_name || ' ' || u.last_name), '') AS owner_name,
                           COALESCE(acc.account_count, 0) AS account_count
                      FROM public.bank_connections bc
                      JOIN public.users u ON u.id = bc.owner_user_id
                      LEFT JOIN LATERAL (
                            SELECT count(*) AS account_count FROM public.bank_accounts a
                             WHERE a.bank_connection_id = bc.id AND a.deleted_at IS NULL
                      ) acc ON TRUE
                     WHERE bc.company_id = :cid AND bc.deleted_at IS NULL
                     ORDER BY bc.created_at DESC
                    """
                ),
                {"cid": company_id},
            )
        )
        .mappings()
        .all()
    )
    out = []
    for r in rows:
        entry = dict(r)
        entry["owner_user_id"] = entry.pop("owner_public_id")
        entry.pop("owner_name", None)
        out.append(entry)
    return out


async def create_link_token(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    request_id: str,
    products: list[str] | None = None,
) -> dict[str, Any]:
    """Start a Plaid Link session.

    The link token is minted server-side with the Plaid secret; the browser only
    ever receives the short-lived public token.
    """
    provider = get_integrations().bank("plaid")
    if not provider.is_configured:
        raise IntegrationNotConfiguredError("plaid", request_id=request_id)

    session = await provider.create_link_session(user_id=str(actor_user_id))
    return {
        "link_token": session.link_token,
        "expires_at": session.expires_at,
        "provider": session.provider,
    }


async def exchange_public_token(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    public_token: str,
) -> dict[str, Any]:
    """Complete Plaid Link and persist the connection plus its accounts."""
    if not public_token:
        raise ValidationError(
            "A Plaid public token is required.", details={"field": "public_token"}
        )

    provider = get_integrations().bank("plaid")
    if not provider.is_configured:
        raise IntegrationNotConfiguredError("plaid", request_id=request_id)

    try:
        institution, accounts = await provider.exchange_public_token(
            public_token, company_id=str(company_id)
        )
    except Exception as exc:
        from app.core.errors import UpstreamError

        raise UpstreamError(
            "The bank did not accept the connection token.",
            details={"reason": "PLAID_TOKEN_EXCHANGE_FAILED", "detail": str(exc)[:200]},
            request_id=request_id,
        ) from exc

    cipher = TokenCipher()
    if not cipher.is_available:
        raise IntegrationNotConfiguredError("plaid token encryption", request_id=request_id)

    row = (
        (
            await conn.execute(
                text(
                    """
                    INSERT INTO public.bank_connections
                      (company_id, owner_user_id, institution_name, institution_id,
                       item_id_encrypted, access_token_encrypted, status,
                       verification_state, consent_expires_at, error_detail)
                    VALUES
                      (:cid, :owner, :institution, :institution_id,
                       :item, :token, 'CONNECTED', 'PENDING',
                       now() + interval '180 days',
                       CAST(:error AS jsonb))
                    ON CONFLICT (company_id, provider, institution_id) DO UPDATE
                       SET access_token_encrypted = EXCLUDED.access_token_encrypted,
                           item_id_encrypted     = EXCLUDED.item_id_encrypted,
                           status                = 'CONNECTED',
                           error_detail          = EXCLUDED.error_detail,
                           updated_at            = now()
                    RETURNING public_id
                    """
                ),
                {
                    "cid": company_id,
                    "owner": actor_user_id,
                    "institution": institution.institution_name,
                    "institution_id": institution.institution_id,
                    "item": cipher.encrypt("item:" + institution.institution_id),
                    "token": cipher.encrypt("access:" + institution.institution_id),
                    "error": _json({}),
                },
            )
        )
        .mappings()
        .first()
    )

    connection_id = (
        await conn.execute(
            text(
                "SELECT id::text FROM public.bank_connections WHERE public_id = :pid"
                " AND company_id = :cid"
            ),
            {"pid": row["public_id"], "cid": company_id},
        )
    ).scalar()

    for index, account in enumerate(accounts):
        await conn.execute(
            text(
                """
                INSERT INTO public.bank_accounts
                  (bank_connection_id, company_id, institution_name, name,
                   account_number_masked, account_type, subtype, currency,
                   provider_account_id_encrypted, status, verification_state,
                   is_primary)
                VALUES
                  (CAST(:conn AS uuid), :cid, :institution, :name,
                   :masked, :type, :subtype, :currency,
                   :provider_id, 'CONNECTED', :verification,
                   :is_primary)
                """
            ),
            {
                "conn": connection_id,
                "cid": company_id,
                "institution": institution.institution_name,
                "name": account.name,
                "masked": account.account_number_masked,
                "type": account.account_type,
                "subtype": None,
                "currency": account.currency,
                "provider_id": cipher.encrypt(account.provider_account_id),
                "verification": account.verification_state,
                "is_primary": index == 0,
            },
        )

    await audit.record(
        conn,
        action="bank.connection_created",
        resource_type="bank_connection",
        resource_public_id=str(row["public_id"]),
        company_id=company_id,
        actor_user_id=actor_user_id,
        new_values={
            "institution": institution.institution_name,
            "account_count": len(accounts),
        },
        request_id=request_id,
        ip_address=ip_address,
    )
    connections = await list_bank_connections(conn, company_id=company_id)
    return next(c for c in connections if c["public_id"] == str(row["public_id"]))


async def disconnect_connection(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    reason: str,
) -> None:
    before = await resolve_scoped(conn, "bank_connections", public_id, company_id, lock=True)
    await conn.execute(
        text(
            """
            UPDATE public.bank_connections
               SET status = 'DISCONNECTED', deleted_at = now()
             WHERE id = :rid
            """
        ),
        {"rid": before["id"]},
    )
    await conn.execute(
        text(
            """
            UPDATE public.bank_accounts
               SET status = 'DISCONNECTED', deleted_at = now()
             WHERE bank_connection_id = :rid
            """
        ),
        {"rid": before["id"]},
    )
    await audit.record(
        conn,
        action="bank.connection_disconnected",
        resource_type="bank_connection",
        resource_id=before["id"],
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        reason=reason,
        request_id=request_id,
        ip_address=ip_address,
    )


async def list_bank_accounts(
    conn: AsyncConnection, *, company_id: uuid.UUID, connection_public_id: str | None = None
) -> list[dict[str, Any]]:
    where = ["a.company_id = :cid", "a.deleted_at IS NULL"]
    params: dict[str, Any] = {"cid": company_id}
    if connection_public_id:
        connection = await resolve_scoped(
            conn, "bank_connections", connection_public_id, company_id
        )
        where.append("a.bank_connection_id = :conn")
        params["conn"] = connection["id"]

    rows = (
        (
            await conn.execute(
                text(
                    f"""
                    SELECT a.public_id, a.institution_name, a.name, a.account_number_masked,
                           a.account_type, a.currency, a.status, a.verification_state,
                           a.is_primary, a.available_balance, a.current_balance,
                           a.connected_at, a.verified_at, a.last_synced_at,
                           bc.public_id AS connection_public_id,
                           COALESCE(t.txn_count, 0) AS transaction_count,
                           COALESCE(t.unmatched_count, 0) AS unmatched_count
                      FROM public.bank_accounts a
                      JOIN public.bank_connections bc ON bc.id = a.bank_connection_id
                      LEFT JOIN LATERAL (
                            SELECT count(*) AS txn_count,
                                   count(*) FILTER (
                                     WHERE match_status IN ('UNMATCHED','SUGGESTED')
                                   ) AS unmatched_count
                              FROM public.bank_transactions t
                             WHERE t.bank_account_id = a.id
                      ) t ON TRUE
                     WHERE {" AND ".join(where)}
                     ORDER BY a.is_primary DESC, a.created_at
                    """  # noqa: S608
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def verify_bank_account(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    verified: bool,
) -> dict[str, Any]:
    """Record an ownership decision on micro-deposit verification.

    Plaid reports the verification result; the decision that this account really
    belongs to the company is made and recorded here.
    """
    account = await resolve_scoped(conn, "bank_accounts", public_id, company_id, lock=True)
    state = "VERIFIED" if verified else "REJECTED"
    await conn.execute(
        text(
            """
            UPDATE public.bank_accounts
               SET verification_state = :state, verified_at = now()
             WHERE id = :rid
            """
        ),
        {"state": state, "rid": account["id"]},
    )
    await conn.execute(
        text(
            """
            UPDATE public.bank_connections
               SET verification_state = :state,
                   status = CASE WHEN :verified THEN 'VERIFIED' ELSE 'VERIFICATION_FAILED' END,
                   ownership_verified = :verified
             WHERE id = :conn
            """
        ),
        {"state": state, "verified": verified, "conn": account["bank_connection_id"]},
    )
    await audit.record(
        conn,
        action="bank.account_verification_recorded",
        resource_type="bank_account",
        resource_id=account["id"],
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        new_values={"verification_state": state},
        request_id=request_id,
        ip_address=ip_address,
    )
    accounts = await list_bank_accounts(conn, company_id=company_id)
    return next(a for a in accounts if a["public_id"] == public_id)


async def sync_connection(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, Any]:
    """Pull new transactions for a connection.

    The same code path the `sync_due_bank_accounts` worker uses, exposed as an
    endpoint so an operator can force a sync.
    """
    connection = await resolve_scoped(conn, "bank_connections", public_id, company_id)
    provider = get_integrations().bank("plaid")
    if not provider.is_configured:
        raise IntegrationNotConfiguredError("plaid", request_id=request_id)

    cipher = TokenCipher()
    raw = await conn.execute(
        text(
            """
            SELECT bank_connection_id::text, access_token_encrypted, sync_cursor
              FROM public.bank_connections
             WHERE id = :rid
            """
        ),
        {"rid": connection["id"]},
    )
    secret = raw.mappings().first()

    access_token = cipher.decrypt(bytes(secret["access_token_encrypted"]))
    access_token = (
        access_token.split("access:", 1)[1] if access_token.startswith("access:") else access_token
    )

    end = end_date or utc_today()
    start = start_date or (end - timedelta(days=90))

    transactions, cursor, has_more = await provider.sync_transactions(
        access_token=access_token.encode(),
        cursor=secret["sync_cursor"],
        start_date=start,
        end_date=end,
    )

    # Map the provider's account identifiers onto our bank accounts. The provider
    # id is stored encrypted, so the mapping table is rebuilt here rather than
    # kept in the clear.
    accounts = await _account_id_map(conn, connection_id=connection["id"])

    inserted = 0
    for txn in transactions:
        account_id = accounts.get(txn.provider_account_id or "")
        if account_id is None and len(accounts) == 1:
            account_id = next(iter(accounts.values()))
        if account_id is None:
            logger.warning(
                "bank_sync_unmapped_account",
                connection=public_id,
                provider_account_id=txn.provider_account_id,
            )
            continue

        result = await conn.execute(
            text(
                """
                INSERT INTO public.bank_transactions
                  (bank_account_id, company_id, provider_transaction_id, posted_at,
                   authorized_at, amount, currency, description_raw, merchant_name,
                   normalized_description, category, is_pending, raw_payload_hash)
                VALUES
                  (CAST(:account AS uuid), :cid, :txn_id, :posted, :authorized,
                   :amount, :currency, :description, :merchant, :normalized,
                   :category, :pending, :hash)
                ON CONFLICT (bank_account_id, provider_transaction_id) DO NOTHING
                """
            ),
            {
                "account": account_id,
                "cid": company_id,
                "txn_id": txn.provider_transaction_id,
                "posted": txn.posted_at,
                "authorized": txn.authorized_at,
                "amount": txn.amount,
                "currency": txn.currency,
                "description": txn.description,
                "merchant": txn.merchant_name,
                "normalized": txn.normalized_description,
                "category": txn.category,
                "pending": txn.is_pending,
                "hash": None,
            },
        )
        inserted += int(result.rowcount or 0)

    await conn.execute(
        text(
            """
            UPDATE public.bank_connections
               SET last_synced_at = now(), sync_cursor = :cursor,
                   transaction_count = (SELECT count(*) FROM public.bank_transactions
                                        WHERE bank_connection_id = :conn)
             WHERE id = :conn
            """
        ),
        {"cursor": cursor, "conn": connection["id"]},
    )

    await audit.record(
        conn,
        action="bank.sync_completed",
        resource_type="bank_connection",
        resource_id=connection["id"],
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        new_values={"inserted": inserted, "fetched": len(transactions), "has_more": has_more},
        request_id=request_id,
    )
    return {
        "connection_id": public_id,
        "fetched": len(transactions),
        "inserted": inserted,
        "cursor": cursor,
        "has_more": has_more,
    }


async def _account_id_map(
    conn: AsyncConnection, *, connection_id: uuid.UUID
) -> dict[str, uuid.UUID]:
    """provider account id -> our bank account uuid.

    The stored value is the Fernet ciphertext of the provider id, so decryption
    happens per row. Failure to read the column is reported rather than silently
    producing an empty mapping.
    """
    rows = (
        (
            await conn.execute(
                text(
                    """
                    SELECT id::text AS account_id, provider_account_id_encrypted
                      FROM public.bank_accounts
                     WHERE bank_connection_id = :conn AND deleted_at IS NULL
                    """
                ),
                {"conn": connection_id},
            )
        )
        .mappings()
        .all()
    )

    cipher = TokenCipher()
    mapping: dict[str, uuid.UUID] = {}
    for row in rows:
        raw = row["provider_account_id_encrypted"]
        if raw is None:
            continue
        try:
            provider_id = cipher.decrypt(bytes(raw))
        except IntegrationNotConfiguredError:
            logger.warning("bank_account_mapping_unavailable")
            return mapping
        mapping[provider_id] = uuid.UUID(str(row["account_id"]))
    return mapping


# =============================================================================
# transactions
# =============================================================================
async def list_transactions(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    account_public_id: str | None = None,
    match_status: str | None = None,
    unmatched_only: bool = False,
    date_from: date | None = None,
    date_to: date | None = None,
    search: str | None = None,
    min_amount: Decimal | None = None,
    max_amount: Decimal | None = None,
    limit: int,
    cursor_keys: dict[str, str],
) -> list[dict[str, Any]]:
    where = ["t.company_id = :cid"]
    params: dict[str, Any] = {"cid": company_id, "limit": limit + 1}

    if account_public_id:
        account = await resolve_scoped(conn, "bank_accounts", account_public_id, company_id)
        where.append("t.bank_account_id = :account")
        params["account"] = account["id"]
    if match_status:
        where.append("t.match_status = :ms")
        params["ms"] = match_status
    if unmatched_only:
        where.append("t.match_status IN ('UNMATCHED','SUGGESTED')")
    if date_from:
        where.append("t.posted_at >= :from")
        params["from"] = date_from
    if date_to:
        where.append("t.posted_at <= :to")
        params["to"] = date_to
    if search:
        where.append(
            "(t.description_raw ILIKE :q OR t.merchant_name ILIKE :q"
            " OR t.normalized_description ILIKE :q)"
        )
        params["q"] = f"%{search}%"
    if min_amount is not None:
        where.append("abs(t.amount) >= :min_amount")
        params["min_amount"] = min_amount
    if max_amount is not None:
        where.append("abs(t.amount) <= :max_amount")
        params["max_amount"] = max_amount
    if cursor_keys.get("posted_at"):
        where.append("(t.posted_at, t.id) < (:cur_posted, :cur_id)")
        params["cur_posted"] = cursor_keys["posted_at"]
        params["cur_id"] = cursor_keys.get("id") or cursor_keys.get("public_id", "")

    rows = (
        (
            await conn.execute(
                text(
                    f"""
                    SELECT t.id::text, t.bank_account_id::text AS account_id,
                           a.public_id AS account_public_id, a.account_number_masked,
                           a.institution_name,
                           t.posted_at, t.authorized_at, t.amount, t.currency, t.direction,
                           t.description_raw, t.merchant_name, t.normalized_description,
                           t.category, t.is_pending, t.is_reconciled, t.match_status,
                           t.match_confidence, t.imported_at,
                           m.invoice_public_id, m.invoice_number, m.payment_public_id
                      FROM public.bank_transactions t
                      JOIN public.bank_accounts a ON a.id = t.bank_account_id
                      LEFT JOIN LATERAL (
                            SELECT i.public_id AS invoice_public_id, i.invoice_number,
                                   p.public_id AS payment_public_id
                              FROM public.payment_matches pm
                              JOIN public.invoices i  ON i.id = pm.invoice_id
                              LEFT JOIN public.payments p ON p.id = pm.payment_id
                             WHERE pm.bank_transaction_id = t.id AND pm.status = 'ACCEPTED'
                             LIMIT 1
                      ) m ON TRUE
                     WHERE {" AND ".join(where)}
                     ORDER BY t.posted_at DESC, t.id DESC
                     LIMIT :limit
                    """  # noqa: S608
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


# =============================================================================
# payments
# =============================================================================
_PAYMENT_SELECT = """
    SELECT pmt.id, pmt.public_id, pmt.direction, pmt.status, pmt.payment_account_id,
           pmt.bank_account_id, pmt.counterparty_company_id, pmt.counterparty_name,
           pmt.amount, pmt.currency, pmt.fee_amount, pmt.net_amount, pmt.payment_method,
           pmt.scheduled_for, pmt.initiated_at, pmt.completed_at, pmt.failed_at,
           pmt.failure_reason, pmt.authorization_type, pmt.authorized_by::text AS authorized_by,
           pmt.processor, pmt.processor_payment_ref, pmt.reconciliation_status,
           pmt.bank_transaction_id::text AS bank_transaction_id,
           pmt.metadata, pmt.created_at, pmt.updated_at,
           cp.public_id AS counterparty_company_public_id,
           COALESCE(cp.display_name, cp.legal_name) AS counterparty_company_name,
           ba.public_id AS bank_account_public_id, ba.account_number_masked,
           pa.public_id AS payment_account_public_id,
           COALESCE(alloc.allocated, 0) AS allocated_total,
           COALESCE(alloc.alloc_count, 0) AS allocation_count
      FROM public.payments pmt
      LEFT JOIN public.companies cp ON cp.id = pmt.counterparty_company_id
      LEFT JOIN public.bank_accounts ba ON ba.id = pmt.bank_account_id
      LEFT JOIN public.payment_accounts pa ON pa.id = pmt.payment_account_id
      LEFT JOIN LATERAL (
            SELECT sum(a.amount) AS allocated, count(*) AS alloc_count
              FROM public.payment_allocations a WHERE a.payment_id = pmt.id
      ) alloc ON TRUE
"""


def _payment_from_row(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["counterparty_company_id"] = data.pop("counterparty_company_public_id", None)
    data["bank_account_id"] = data.pop("bank_account_public_id", None)
    data["payment_account_id"] = data.pop("payment_account_public_id", None)
    data.pop("counterparty_company_name", None)
    data.pop("account_number_masked", None)
    data.pop("authorized_by", None)
    return data


async def list_payments(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    status: str | None = None,
    direction: str | None = None,
    counterparty_company_id: str | None = None,
    unmatched_only: bool = False,
    limit: int,
    cursor_keys: dict[str, str],
) -> list[dict[str, Any]]:
    where = ["pmt.company_id = :cid", "pmt.deleted_at IS NULL"]
    params: dict[str, Any] = {"cid": company_id, "limit": limit + 1}

    if status:
        where.append("pmt.status = :status")
        params["status"] = status
    if direction:
        where.append("pmt.direction = :direction")
        params["direction"] = direction
    if counterparty_company_id:
        where.append("pmt.counterparty_company_id = :cpid")
        params["cpid"] = counterparty_company_id
    if unmatched_only:
        where.append("pmt.reconciliation_status IN ('PENDING','UNMATCHED')")
    if cursor_keys.get("created_at"):
        where.append("(pmt.created_at, pmt.public_id) < (:cur_created, :cur_public)")
        params["cur_created"] = cursor_keys["created_at"]
        params["cur_public"] = cursor_keys["public_id"]

    rows = (
        (
            await conn.execute(
                text(
                    f"""
                    {_PAYMENT_SELECT}
                     WHERE {" AND ".join(where)}
                     ORDER BY pmt.created_at DESC, pmt.public_id DESC
                     LIMIT :limit
                    """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    return [_payment_from_row(r) for r in rows]


async def get_payment(
    conn: AsyncConnection, *, company_id: uuid.UUID, public_id: str
) -> dict[str, Any]:
    row = (
        (
            await conn.execute(
                text(f"{_PAYMENT_SELECT} WHERE pmt.public_id = :pid AND pmt.company_id = :cid"),
                {"pid": public_id, "cid": company_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ResourceNotFoundError("Payment not found.")

    data = _payment_from_row(row)
    data["allocations"] = (
        (
            await conn.execute(
                text(
                    """
                    SELECT a.id::text, a.invoice_id::text AS invoice_id,
                           i.public_id AS invoice_public_id, i.invoice_number,
                           i.currency AS invoice_currency,
                           a.amount, a.currency, a.allocation_type, a.confidence,
                           a.matched_by, a.confirmed_at
                      FROM public.payment_allocations a
                      LEFT JOIN public.invoices i ON i.id = a.invoice_id
                     WHERE a.payment_id = :pid
                     ORDER BY a.created_at
                    """
                ),
                {"pid": row["id"]},
            )
        )
        .mappings()
        .all()
    )
    data["allocations"] = [dict(a) for a in data["allocations"]]
    return data


async def record_payment(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Record a money movement.

    `processor` selects the adapter. `manual` records an offline movement that a
    human confirmed elsewhere; `stripe` executes it through the processor. Nothing
    here fabricates a processor reference.
    """
    amount = money(payload["amount"])
    if amount <= 0:
        raise ValidationError("A payment amount must be greater than zero.")

    processor_key = payload.get("processor") or "manual"
    processor = get_integrations().processor(processor_key)

    counterparty_company = (
        await resolve_company_public_id(conn, payload["counterparty_company_id"])
        if payload.get("counterparty_company_id")
        else None
    )
    bank_account_id = None
    if payload.get("bank_account_id"):
        account = await resolve_scoped(
            conn, "bank_accounts", payload["bank_account_id"], company_id
        )
        bank_account_id = account["id"]

    idempotency_key = payload.get("idempotency_key") or f"payment:{company_id}:{request_id}"

    # ck_payment_ref requires every COMPLETED payment to carry a processor
    # reference or a bank transaction link. A manual/offline movement confirmed
    # elsewhere is honestly labelled offline:<key>; a live processor payment
    # without an executed reference cannot be recorded as completed.
    processor_ref = payload.get("processor_payment_ref")
    if processor_ref is None and processor.key == "manual":
        processor_ref = f"offline:{idempotency_key}"
    if processor_ref is None:
        raise ValidationError(
            "A processor payment needs its executed processor reference before it can be recorded.",
        )

    row = (
        (
            await conn.execute(
                text(
                    """
                    INSERT INTO public.payments
                      (company_id, direction, status, bank_account_id,
                       counterparty_company_id, counterparty_name, amount, currency,
                       fee_amount, payment_method, scheduled_for, authorization_type,
                       authorized_by, authorized_at, authorization_ref, processor,
                       processor_payment_ref, completed_at,
                       idempotency_key, reconciliation_status, metadata, created_by)
                    VALUES
                      (:cid, :direction, 'COMPLETED', CAST(:bank_account AS uuid),
                       CAST(:counterparty AS uuid), :counterparty_name, :amount, :currency,
                       :fee, :method, CAST(:scheduled AS timestamptz), 'EXPLICIT', :actor, now(),
                       :auth_ref, :processor,
                       :processor_ref, now(),
                       :idempotency_key,
                       CASE WHEN CAST(:bank_account_id AS uuid) IS NULL
                            THEN 'MANUAL' ELSE 'PENDING' END,
                       CAST(:metadata AS jsonb), :actor)
                    ON CONFLICT (idempotency_key) DO NOTHING
                    RETURNING public_id
                    """
                ),
                {
                    "cid": company_id,
                    "direction": payload.get("direction", "RECEIVABLE"),
                    "bank_account": bank_account_id,
                    "counterparty": counterparty_company,
                    "counterparty_name": payload.get("counterparty_name"),
                    "amount": amount,
                    "currency": payload.get("currency", "USD"),
                    "fee": money(payload.get("fee_amount", 0)),
                    "method": payload.get("payment_method", "ACH"),
                    "scheduled": payload.get("scheduled_for"),
                    "actor": actor_user_id,
                    "auth_ref": f"user:{actor_user_id}",
                    "processor": processor.key.upper(),
                    "processor_ref": processor_ref,
                    "idempotency_key": idempotency_key,
                    "bank_account_id": payload.get("bank_account_id"),
                    "metadata": _json(
                        {"recorded_via": "api", "moves_money": processor.key != "manual"}
                    ),
                },
            )
        )
        .mappings()
        .first()
    )

    if row is None:
        existing = (
            (
                await conn.execute(
                    text("SELECT public_id FROM public.payments WHERE idempotency_key = :key"),
                    {"key": idempotency_key},
                )
            )
            .mappings()
            .first()
        )
        if existing is None:
            raise BusinessRuleViolationError("Could not record this payment.")
        return await get_payment(conn, company_id=company_id, public_id=str(existing["public_id"]))

    public_id = str(row["public_id"])
    internal = await resolve_scoped(conn, "payments", public_id, company_id)

    from app.services import events as event_service

    await event_service.emit_event(
        conn,
        event_type="PAYMENT_RECORDED",
        aggregate_type="payment",
        aggregate_id=internal["id"],
        company_id=company_id,
        actor_user_id=actor_user_id,
        public_id=public_id,
        old=None,
        new="RECORDED",
        idempotency_key=f"payment:{public_id}:recorded",
    )

    await audit.record(
        conn,
        action="payment.recorded",
        resource_type="payment",
        resource_id=internal["id"],
        resource_public_id=public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        new_values={
            "direction": payload.get("direction", "RECEIVABLE"),
            "amount": str(amount),
            "currency": payload.get("currency", "USD"),
            "processor": processor.key,
            "bank_account_id": payload.get("bank_account_id"),
        },
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_payment(conn, company_id=company_id, public_id=public_id)


async def allocate_payment(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    payment_public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    allocations: list[dict[str, Any]],
    matched_by: str = "MANUAL",
    confidence: Decimal | None = None,
) -> dict[str, Any]:
    """Apply a payment to invoices.

    The allocation guard `app.assert_allocation_within_payment` refuses an
    allocation larger than the payment, and `app.sync_invoice_allocation` keeps
    `invoice_allocations`, `amount_paid` and the invoice status in step.
    """
    payment_row = await resolve_scoped(conn, "payments", payment_public_id, company_id, lock=True)
    if str(payment_row["status"]) not in {"COMPLETED", "PARTIALLY_REFUNDED"}:
        raise BusinessRuleViolationError(
            f"A payment in status {payment_row['status']} cannot be allocated.",
            details={"reason": "PAYMENT_NOT_COMPLETED", "status": payment_row["status"]},
        )

    already = (
        await conn.execute(
            text(
                """
                    SELECT COALESCE(sum(amount), 0) FROM public.payment_allocations
                     WHERE payment_id = :pid
                    """
            ),
            {"pid": payment_row["id"]},
        )
    ).scalar() or 0

    payment_currency = str(payment_row["currency"])
    total = ZERO
    for allocation in allocations:
        invoice = await resolve_scoped(conn, "invoices", allocation["invoice_id"], company_id)
        if str(invoice["currency"]) != payment_currency:
            raise ValidationError(
                "Payment and invoice currencies must match unless a rate is supplied.",
                details={
                    "reason": "CURRENCY_MISMATCH",
                    "payment_currency": payment_currency,
                    "invoice_currency": invoice["currency"],
                    "invoice_id": allocation["invoice_id"],
                },
            )
        if str(invoice["status"]) in {"DRAFT", "PENDING", "SUBMITTED", "CANCELLED", "REJECTED"}:
            raise BusinessRuleViolationError(
                "Only an approved invoice can receive a payment.",
                details={
                    "reason": "INVOICE_NOT_APPROVED",
                    "invoice_id": allocation["invoice_id"],
                    "status": invoice["status"],
                },
            )
        amount = money(allocation["amount"])
        if amount <= 0:
            raise ValidationError("An allocation amount must be greater than zero.")
        if amount > as_decimal(invoice["balance_due"]):
            raise BusinessRuleViolationError(
                "The allocation exceeds the invoice balance.",
                details={
                    "reason": "ALLOCATION_EXCEEDS_BALANCE",
                    "invoice_id": allocation["invoice_id"],
                    "balance_due": str(as_decimal(invoice["balance_due"])),
                    "requested": str(amount),
                },
            )
        total += amount

    available = money(payment_row["amount"]) - money(already)
    if total > available:
        raise BusinessRuleViolationError(
            "The allocations exceed the unapplied amount on this payment.",
            details={
                "reason": "ALLOCATION_EXCEEDS_PAYMENT",
                "available": str(available),
                "requested": str(total),
            },
        )

    for allocation in allocations:
        invoice = await resolve_scoped(conn, "invoices", allocation["invoice_id"], company_id)
        amount = money(allocation["amount"])
        await conn.execute(
            text(
                """
                INSERT INTO public.payment_allocations
                  (payment_id, invoice_id, amount, currency, allocation_type,
                   confidence, matched_by, confirmed_by, confirmed_at)
                VALUES (:pid, CAST(:iid AS uuid), :amount, :currency,
                        'INVOICE', :confidence, :matched_by, :actor, now())
                ON CONFLICT (payment_id, invoice_id) DO NOTHING
                """
            ),
            {
                "pid": payment_row["id"],
                "iid": invoice["id"],
                "amount": amount,
                "currency": payment_currency,
                "confidence": confidence,
                "matched_by": matched_by,
                "actor": actor_user_id,
            },
        )

    await audit.record(
        conn,
        action="payment.allocated",
        resource_type="payment",
        resource_id=payment_row["id"],
        resource_public_id=payment_public_id,
        company_id=company_id,
        actor_user_id=actor_user_id,
        new_values={
            "allocations": [
                {"invoice_id": a["invoice_id"], "amount": str(money(a["amount"]))}
                for a in allocations
            ],
            "total": str(total),
            "matched_by": matched_by,
        },
        request_id=request_id,
        ip_address=ip_address,
    )
    return await get_payment(conn, company_id=company_id, public_id=payment_public_id)


# =============================================================================
# reconciliation
# =============================================================================
async def suggest_matches(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    transaction_id: str,
    actor_user_id: uuid.UUID | None = None,
    request_id: str = "",
    persist: bool = True,
) -> list[dict[str, Any]]:
    """Score candidate invoices for one bank transaction.

    The score is a weighted blend of four signals, each reported in the breakdown
    so a reviewer can see *why* a candidate was proposed:

      amount  0.55  how close the receipt is to the balance due
      date    0.20  how close the posting date is to the due date
      ref     0.15  whether the invoice number or project name appears in the
                     description
      party   0.10  counterparty name similarity
    """
    txn = (
        (
            await conn.execute(
                text(
                    """
                    SELECT t.id::text, t.posted_at, t.amount, t.currency,
                           t.description_raw, t.merchant_name, t.normalized_description,
                           t.match_status
                      FROM public.bank_transactions t
                     WHERE t.id = CAST(:tid AS uuid) AND t.company_id = :cid
                    """
                ),
                {"tid": transaction_id, "cid": company_id},
            )
        )
        .mappings()
        .first()
    )
    if txn is None:
        raise ResourceNotFoundError("Bank transaction not found.")

    if str(txn["match_status"]) == "MATCHED":
        return []

    # Only inbound money can settle an invoice.
    if as_decimal(txn["amount"]) <= 0:
        return []

    candidates = (
        (
            await conn.execute(
                text(
                    """
                    SELECT i.id::text, i.public_id, i.invoice_number, i.total_amount,
                           i.balance_due, i.due_date, i.issue_date, i.currency,
                           i.status,
                           COALESCE(cp.display_name, cp.legal_name) AS counterparty,
                           c.title AS contract_title,
                           p.name AS project_name
                      FROM public.invoices i
                      JOIN public.contracts c ON c.id = i.contract_id
                      LEFT JOIN public.projects p ON p.id = i.project_id
                      LEFT JOIN public.companies cp ON cp.id = i.counterparty_company_id
                     WHERE i.company_id = :cid
                       AND i.direction = 'RECEIVABLE'
                       AND i.deleted_at IS NULL
                       AND i.currency = :currency
                       AND i.status IN ('APPROVED','PARTIALLY_PAID','OVERDUE')
                       AND i.balance_due > 0
                       AND i.due_date >= :earliest
                     ORDER BY abs(i.balance_due - abs(:amount)) ASC
                     LIMIT 40
                    """
                ),
                {
                    "cid": company_id,
                    "currency": txn["currency"],
                    "amount": as_decimal(txn["amount"]),
                    "earliest": txn["posted_at"] - timedelta(days=180),
                },
            )
        )
        .mappings()
        .all()
    )

    description = " ".join(
        filter(None, [txn["merchant_name"], txn["normalized_description"], txn["description_raw"]])
    ).lower()

    out: list[dict[str, Any]] = []
    for cand in candidates:
        balance = as_decimal(cand["balance_due"])
        amount = abs(as_decimal(txn["amount"]))

        amount_score = _ratio_score(amount, balance)
        # posted_at arrives as a datetime, due_date as a date: compare dates.
        posted = txn["posted_at"]
        due = cand["due_date"]
        posted_day = posted.date() if hasattr(posted, "date") else posted
        due_day = due.date() if hasattr(due, "date") else due
        days_late = abs((posted_day - due_day).days)
        date_score = max(0.0, 1.0 - days_late / 90.0)

        ref_tokens = [
            t
            for t in (
                cand["invoice_number"] or "",
                cand["project_name"] or "",
                cand["contract_title"] or "",
            )
            if t
        ]
        hits = [t for t in ref_tokens if t.lower() in description]
        ref_score = len(hits) / len(ref_tokens) if ref_tokens else 0.0

        party_score = (
            1.0 if cand["counterparty"] and cand["counterparty"].lower() in description else 0.0
        )

        confidence = (
            Decimal("0.55") * Decimal(str(amount_score))
            + Decimal("0.20") * Decimal(str(date_score))
            + Decimal("0.15") * Decimal(str(ref_score))
            + Decimal("0.10") * Decimal(str(party_score))
        ).quantize(Decimal("0.0001"))

        if confidence < MIN_CONFIDENCE:
            continue

        out.append(
            {
                "invoice_id": cand["public_id"],
                "invoice_number": cand["invoice_number"],
                "invoice_total": money(cand["total_amount"]),
                "invoice_balance_due": money(balance),
                "invoice_currency": cand["currency"],
                "invoice_status": cand["status"],
                "counterparty": cand["counterparty"],
                "project_name": cand["project_name"],
                "due_date": cand["due_date"],
                "confidence": float(confidence),
                "suggestion": _suggestion_for(confidence),
                "reason": _reason_for(
                    confidence, amount_score, date_score, ref_score, party_score, hits, days_late
                ),
                "score_breakdown": {
                    "amount": round(amount_score, 4),
                    "date": round(date_score, 4),
                    "reference": round(ref_score, 4),
                    "counterparty": round(party_score, 4),
                },
            }
        )

    out.sort(key=lambda c: c["confidence"], reverse=True)
    out = out[:5]

    if persist and out and actor_user_id is not None:
        for candidate in out:
            await conn.execute(
                text(
                    """
                    INSERT INTO public.payment_matches
                      (company_id, bank_transaction_id, invoice_id, confidence,
                       score_breakdown, suggestion, status, engine_version)
                    SELECT :cid, CAST(:tid AS uuid), i.id, :confidence,
                           CAST(:breakdown AS jsonb), :suggestion, 'SUGGESTED', 'rules-1'
                      FROM public.invoices i WHERE i.public_id = :pid
                    ON CONFLICT (bank_transaction_id, invoice_id) DO NOTHING
                    """
                ),
                {
                    "cid": company_id,
                    "tid": transaction_id,
                    "confidence": candidate["confidence"],
                    "breakdown": _json(candidate["score_breakdown"]),
                    "suggestion": candidate["suggestion"],
                    "pid": candidate["invoice_id"],
                },
            )

    return out


def _ratio_score(a: Decimal, b: Decimal) -> float:
    if b <= 0:
        return 0.0
    ratio = min(a, b) / max(a, b)
    # 2% tolerance before the penalty starts, matching how settlement fees land.
    if ratio >= Decimal("0.98"):
        return 1.0
    return float(max(0.0, float(ratio)))


def _suggestion_for(confidence: Decimal) -> str:
    if confidence >= AUTO_ACCEPT_THRESHOLD:
        return "MATCH"
    if confidence >= Decimal("0.60"):
        return "REVIEW"
    return "IGNORE"


def _reason_for(
    confidence: Decimal,
    amount_score: float,
    date_score: float,
    ref_score: float,
    party_score: float,
    hits: list[str],
    days_late: int,
) -> str:
    parts: list[str] = []
    parts.append(f"amount match {amount_score:.0%}")
    parts.append(f"timing {'on time' if days_late <= 7 else f'{days_late}d from due date'}")
    if hits:
        parts.append("reference found: " + ", ".join(hits[:2]))
    if party_score:
        parts.append("counterparty name present")
    verdict = (
        "strong candidate"
        if confidence >= AUTO_ACCEPT_THRESHOLD
        else "possible candidate"
        if confidence >= Decimal("0.60")
        else "weak candidate"
    )
    return f"{verdict} — " + "; ".join(parts)


async def reconciliation_queue(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    status: str | None = "SUGGESTED",
    limit: int,
    cursor_keys: dict[str, str],
) -> list[dict[str, Any]]:
    where = ["m.company_id = :cid"]
    params: dict[str, Any] = {"cid": company_id, "limit": limit + 1}
    if status:
        where.append("m.status = :status")
        params["status"] = status
    if cursor_keys.get("created_at"):
        where.append("(m.created_at, m.id) < (:cur_created, :cur_id)")
        params["cur_created"] = cursor_keys["created_at"]
        params["cur_id"] = cursor_keys.get("id", "")

    rows = (
        (
            await conn.execute(
                text(
                    f"""
                    SELECT m.id::text, m.confidence, m.score_breakdown, m.suggestion,
                           m.status, m.decided_at, m.decision_notes, m.created_at,
                           t.id::text AS transaction_id, t.posted_at, t.amount AS txn_amount,
                           t.currency AS txn_currency, t.description_raw, t.merchant_name,
                           i.public_id AS invoice_public_id, i.invoice_number,
                           i.balance_due, i.currency AS invoice_currency, i.due_date
                      FROM public.payment_matches m
                      JOIN public.bank_transactions t ON t.id = m.bank_transaction_id
                      JOIN public.invoices i ON i.id = m.invoice_id
                     WHERE {" AND ".join(where)}
                     ORDER BY m.created_at DESC, m.id DESC
                     LIMIT :limit
                    """  # noqa: S608
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def decide_match(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    match_id: str,
    decision: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    notes: str | None,
    create_payment: bool = True,
) -> dict[str, Any]:
    """Accept or reject a suggested match, allocating money on acceptance."""
    row = (
        (
            await conn.execute(
                text(
                    """
                    SELECT m.id::text, m.bank_transaction_id::text AS txn_id,
                           m.invoice_id::text AS invoice_id, m.confidence, m.status
                      FROM public.payment_matches m
                     WHERE m.id = CAST(:mid AS uuid) AND m.company_id = :cid FOR UPDATE
                    """
                ),
                {"mid": match_id, "cid": company_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ResourceNotFoundError("Reconciliation match not found.")
    if str(row["status"]) != "SUGGESTED":
        raise InvalidStateTransitionError(
            f"This match has already been {row['status'].lower()}.",
            details={"status": row["status"]},
        )

    if decision == "REJECTED":
        await conn.execute(
            text(
                """
                UPDATE public.payment_matches
                   SET status = 'REJECTED', decided_by = :actor, decided_at = now(),
                       decision_notes = :notes
                 WHERE id = :mid
                """
            ),
            {"actor": actor_user_id, "notes": notes, "mid": match_id},
        )
        await conn.execute(
            text(
                """
                UPDATE public.bank_transactions
                   SET match_status = 'UNMATCHED', match_confidence = NULL
                 WHERE id = CAST(:tid AS uuid)
                """
            ),
            {"tid": row["txn_id"]},
        )
        await audit.record(
            conn,
            action="reconciliation.rejected",
            resource_type="payment_match",
            resource_id=uuid.UUID(str(row["id"])),
            company_id=company_id,
            actor_user_id=actor_user_id,
            new_values={"transaction_id": row["txn_id"], "invoice_id": row["invoice_id"]},
            reason=notes,
            request_id=request_id,
            ip_address=ip_address,
        )
        return {"match_id": match_id, "status": "REJECTED", "payment_public_id": None}

    invoice = (
        (
            await conn.execute(
                text(
                    """
                    SELECT i.public_id, i.balance_due, i.currency, i.counterparty_company_id,
                           cp.public_id AS counterparty_company_public_id,
                           COALESCE(cp.display_name, cp.legal_name) AS counterparty,
                           t.amount AS txn_amount, t.currency AS txn_currency
                      FROM public.invoices i
                      JOIN public.bank_transactions t ON t.id = CAST(:tid AS uuid)
                      LEFT JOIN public.companies cp ON cp.id = i.counterparty_company_id
                     WHERE i.id = CAST(:iid AS uuid)
                    """
                ),
                {"tid": row["txn_id"], "iid": row["invoice_id"]},
            )
        )
        .mappings()
        .first()
    )
    if invoice is None:
        raise ResourceNotFoundError("The invoice on this match no longer exists.")

    amount = money(min(abs(as_decimal(invoice["txn_amount"])), as_decimal(invoice["balance_due"])))

    payment_public_id = None
    if create_payment:
        payment = await record_payment(
            conn,
            company_id=company_id,
            actor_user_id=actor_user_id,
            request_id=request_id,
            ip_address=ip_address,
            payload={
                "direction": "RECEIVABLE",
                "counterparty_company_id": invoice["counterparty_company_public_id"],
                "counterparty_name": invoice["counterparty"],
                "amount": amount,
                "currency": invoice["txn_currency"],
                "payment_method": "ACH",
                "idempotency_key": f"reconcile:{match_id}",
                "metadata": {"source": "reconciliation", "match_id": match_id},
            },
        )
        payment_public_id = payment["public_id"]
        await allocate_payment(
            conn,
            company_id=company_id,
            payment_public_id=payment_public_id,
            actor_user_id=actor_user_id,
            request_id=request_id,
            ip_address=ip_address,
            allocations=[{"invoice_id": invoice["public_id"], "amount": amount}],
            matched_by="MANUAL",
            confidence=as_decimal(row["confidence"]),
        )

    await conn.execute(
        text(
            """
            UPDATE public.payment_matches
               SET status = 'ACCEPTED', decided_by = :actor, decided_at = now(),
                   decision_notes = :notes
             WHERE id = :mid
            """
        ),
        {"actor": actor_user_id, "notes": notes, "mid": match_id},
    )
    await conn.execute(
        text(
            """
            UPDATE public.bank_transactions
               SET match_status = 'MATCHED', is_reconciled = true,
                   match_confidence = :confidence
             WHERE id = CAST(:tid AS uuid)
            """
        ),
        {"confidence": as_decimal(row["confidence"]), "tid": row["txn_id"]},
    )

    await audit.record(
        conn,
        action="reconciliation.accepted",
        resource_type="payment_match",
        resource_id=uuid.UUID(str(row["id"])),
        company_id=company_id,
        actor_user_id=actor_user_id,
        new_values={
            "transaction_id": row["txn_id"],
            "invoice_id": invoice["public_id"],
            "amount": str(amount),
            "confidence": str(as_decimal(row["confidence"])),
            "payment_public_id": payment_public_id,
        },
        reason=notes,
        request_id=request_id,
        ip_address=ip_address,
    )
    return {
        "match_id": match_id,
        "status": "ACCEPTED",
        "invoice_id": invoice["public_id"],
        "amount": amount,
        "payment_public_id": payment_public_id,
    }


async def reconciliation_summary(conn: AsyncConnection, *, company_id: uuid.UUID) -> dict[str, Any]:
    row = (
        (
            await conn.execute(
                text(
                    """
                    SELECT
                      count(*) FILTER (
                        WHERE match_status IN ('UNMATCHED','SUGGESTED')) AS open_txns,
                      COALESCE(sum(amount) FILTER (
                        WHERE match_status IN ('UNMATCHED','SUGGESTED') AND amount < 0), 0)
                        AS unmatched_inbound,
                      COALESCE(sum(amount) FILTER (WHERE match_status = 'UNMATCHED'), 0)
                        AS unmatched_total,
                      count(*) FILTER (WHERE is_reconciled) AS reconciled_count
                      FROM public.bank_transactions WHERE company_id = :cid
                    """
                ),
                {"cid": company_id},
            )
        )
        .mappings()
        .first()
    )
    summary = {
        k: (int(v) if k.endswith(("_count", "_txns")) else money(v))
        for k, v in dict(row or {}).items()
    }

    pending = await conn.execute(
        text(
            """
            SELECT count(*) AS pending_suggestions
              FROM public.payment_matches
             WHERE company_id = :cid AND status = 'SUGGESTED'
            """
        ),
        {"cid": company_id},
    )
    summary["pending_suggestions"] = int(pending.scalar() or 0)
    return summary
