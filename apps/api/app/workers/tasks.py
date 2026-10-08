"""Background tasks.

Every task here is idempotent, because Celery delivery is at-least-once: a task
that runs twice must produce the same state as a task that runs once. That is why
bank transactions rely on a unique provider id, invoice generation uses a
billing-run idempotency key, and webhook handling claims its event row before any
effect.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from app.core.clock import utc_today
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import session_scope
from app.workers.celery_app import async_task, celery_app

logger = get_logger(__name__)
settings = get_settings()


# ---------------------------------------------------------------- outbox drain
@async_task(name="app.workers.tasks.dispatch_outbox", queue="critical")
async def dispatch_outbox(batch_size: int = 200) -> dict[str, int]:
    """Publish committed outbox events to their queues.

    The outbox row and the domain change commit together, so an event can never be
    lost. Publishing is skipped if the row is already marked, and a failure leaves
    `next_attempt_at` to be retried with backoff.
    """
    published = 0
    failed = 0

    async with session_scope() as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        """
                    SELECT id, event_type, aggregate_type, aggregate_id, payload,
                           attempts
                      FROM platform.outbox_events
                     WHERE published_at IS NULL
                       AND next_attempt_at <= now()
                     ORDER BY created_at
                     LIMIT :limit
                    """
                    ),
                    {"limit": batch_size},
                )
            )
            .mappings()
            .all()
        )

    for row in rows:
        try:
            # Routing is by event family so a slow consumer cannot block others.
            queue = "critical" if str(row["event_type"]).endswith("APPROVED") else "default"
            celery_app.send_task(
                "app.workers.tasks.handle_domain_event",
                args=[
                    {
                        "event_id": str(row["id"]),
                        "event_type": row["event_type"],
                        "aggregate_type": row["aggregate_type"],
                        "aggregate_id": str(row["aggregate_id"]),
                        "payload": row["payload"],
                    }
                ],
                queue=queue,
            )
            async with session_scope() as conn:
                await conn.execute(
                    text(
                        """
                        UPDATE platform.outbox_events
                           SET published_at = now(), attempts = attempts + 1
                         WHERE id = :id AND published_at IS NULL
                        """
                    ),
                    {"id": row["id"]},
                )
            published += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            async with session_scope() as conn:
                # Exponential backoff, capped at an hour.
                await conn.execute(
                    text(
                        """
                        UPDATE platform.outbox_events
                           SET attempts = attempts + 1,
                               last_error = :err,
                               next_attempt_at = now() + least(
                                   interval '1 hour',
                                   (interval '10 seconds' * power(2, least(attempts, 10)))
                               )
                         WHERE id = :id
                        """
                    ),
                    {"id": row["id"], "err": str(exc)[:500]},
                )
            # `event` is already the structlog event name, so the outbox row
            # id is passed under a distinct key rather than colliding with it.
            logger.warning(
                "outbox_publish_failed",
                outbox_id=str(row["id"]),
                error=str(exc)[:200],
            )

    return {"published": published, "failed": failed}


@async_task(name="app.workers.tasks.handle_domain_event")
async def handle_domain_event(event: dict[str, Any]) -> None:
    """Process one domain event.

    Consumers are idempotent by construction: notifications are keyed on
    (user, resource, type); billing runs carry their own idempotency key.
    """
    from app.services.events import handle_event

    await handle_event(event)


# ------------------------------------------------------------------- banking
@async_task(name="app.workers.tasks.sync_due_bank_accounts", queue="scheduled")
async def sync_due_bank_accounts() -> dict[str, int]:
    """Import transactions for connected accounts that are due for a sync."""
    from app.integrations.payments import get_integrations

    async with session_scope() as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        """
                    SELECT c.id::text AS connection_id, c.company_id::text AS company_id,
                           c.public_id, c.access_token_encrypted, c.sync_cursor,
                           c.last_synced_at
                      FROM public.bank_connections c
                     WHERE c.deleted_at IS NULL
                       AND c.status IN ('CONNECTED', 'VERIFIED')
                       AND (c.last_synced_at IS NULL
                            OR c.last_synced_at < now() - interval '15 minutes')
                     LIMIT 50
                    """
                    )
                )
            )
            .mappings()
            .all()
        )

    provider = get_integrations().bank("plaid")
    if not provider.is_configured:
        logger.info("bank_sync_skipped", reason="plaid_not_configured")
        return {"synced": 0, "skipped": len(rows)}

    imported = 0
    accounts = 0

    for row in rows:
        try:
            end = utc_today()
            start = (
                row["last_synced_at"] or dt.datetime.now(dt.UTC) - dt.timedelta(days=90)
            ).date()
            transactions, cursor, _has_more = await provider.sync_transactions(
                access_token=row["access_token_encrypted"],
                cursor=row["sync_cursor"],
                start_date=start,
                end_date=end,
            )

            async with session_scope() as conn:
                for txn in transactions:
                    # ON CONFLICT DO NOTHING makes re-sync a no-op, which is what
                    # keeps a redelivered task from duplicating money records.
                    result = await conn.execute(
                        text(
                            """
                            INSERT INTO public.bank_transactions
                              (bank_account_id, company_id, provider_transaction_id,
                               posted_at, amount, currency, description_raw,
                               merchant_name, category, is_pending, raw_payload_hash)
                            SELECT ba.id, :cid, :ptid, :posted, :amount, :ccy,
                                   :desc, :merchant, :category, :pending, :hash
                              FROM public.bank_accounts ba
                             WHERE ba.bank_connection_id = CAST(:conn AS uuid)
                               AND ba.provider_account_id_encrypted IS NOT NULL
                            ON CONFLICT (bank_account_id, provider_transaction_id) DO NOTHING
                            RETURNING id
                            """
                        ),
                        {
                            "cid": row["company_id"],
                            "conn": row["connection_id"],
                            "ptid": txn.provider_transaction_id,
                            "posted": txn.posted_at,
                            "amount": txn.amount,
                            "ccy": txn.currency,
                            "desc": txn.description,
                            "merchant": txn.merchant_name,
                            "category": txn.category,
                            "pending": txn.is_pending,
                            "hash": None,
                        },
                    )
                    if result.rowcount:
                        imported += 1

                await conn.execute(
                    text(
                        """
                        UPDATE public.bank_connections
                           SET sync_cursor = :cursor, last_synced_at = now(),
                               transaction_count = transaction_count + :n
                         WHERE id = CAST(:conn AS uuid)
                        """
                    ),
                    {"cursor": cursor, "n": imported, "conn": row["connection_id"]},
                )
                accounts += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("bank_sync_failed", connection=row["public_id"], error=str(exc)[:200])

    logger.info("bank_sync_complete", accounts=accounts, transactions=imported)
    return {"accounts": accounts, "transactions": imported}


# ------------------------------------------------------------------ billing
@async_task(name="app.workers.tasks.generate_invoices", queue="bulk")
async def generate_invoices(billing_run_public_id: str) -> dict[str, Any]:
    """Run the invoice engine for one billing run.

    Idempotent: the run's own key plus the unique index on
    (contract, direction, period, currency) means a duplicate delivery cannot
    produce a second invoice for the same period.
    """
    from app.services.billing import run_billing

    return await run_billing(billing_run_public_id)


@async_task(name="app.workers.tasks.mark_invoices_overdue", queue="scheduled")
async def mark_invoices_overdue() -> dict[str, int]:
    """Transition overdue invoices. A state transition, not a recomputation."""
    async with session_scope() as conn:
        result = await conn.execute(text("SELECT app.mark_overdue_invoices() AS n"))
        count = int(result.scalar() or 0)

    if count:
        logger.info("invoices_marked_overdue", count=count)
    return {"marked": count}


# ------------------------------------------------------------------ schedules
@async_task(name="app.workers.tasks.advance_payment_schedules", queue="scheduled")
async def advance_payment_schedules() -> dict[str, int]:
    """Create the next occurrence of each due recurring payment schedule.

    Creating a SCHEDULED payment is not moving money: the occurrence still needs
    its own authorization record before it can be initiated.
    """
    created = 0

    async with session_scope() as conn:
        due = (
            (
                await conn.execute(
                    text(
                        """
                    SELECT id::text, company_id::text, public_id, amount, currency,
                           payment_method, contract_id::text, invoice_id::text,
                           next_run_date
                      FROM public.payment_schedules
                     WHERE status = 'ACTIVE'
                       AND deleted_at IS NULL
                       AND next_run_date <= current_date
                     LIMIT 200
                    """
                    )
                )
            )
            .mappings()
            .all()
        )

        for row in due:
            result = await conn.execute(
                text(
                    """
                    INSERT INTO public.payments
                      (public_id, company_id, direction, status, amount, currency,
                       payment_method, scheduled_for, authorization_type, processor,
                       metadata, idempotency_key)
                    VALUES
                      ('PM' || upper(substr(md5(random()::text), 1, 6)), CAST(:cid AS uuid),
                       'PAYABLE', 'SCHEDULED', :amount, :ccy, :method, current_date,
                       'SCHEDULED', 'MANUAL',
                       jsonb_build_object(
                         'schedule_public_id', CAST(:pid AS text), 'source', 'recurring'),
                       :idem)
                    ON CONFLICT (idempotency_key) DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "cid": row["company_id"],
                    "amount": row["amount"] or Decimal("0"),
                    "ccy": row["currency"],
                    "method": row["payment_method"],
                    "pid": row["public_id"],
                    # One occurrence per schedule per day.
                    "idem": f"schedule:{row['public_id']}:{row['next_run_date']}",
                },
            )
            if result.rowcount:
                created += 1

            await conn.execute(
                text("SELECT app.advance_schedule(CAST(:id AS uuid), current_date)"),
                {"id": row["id"]},
            )

    if created:
        logger.info("payment_schedules_advanced", created=created)
    return {"created": created}


# ------------------------------------------------------------------ compliance
@async_task(name="app.workers.tasks.scan_contract_expiries", queue="bulk")
async def scan_contract_expiries() -> dict[str, int]:
    """Raise expiry alerts so they exist before a deadline is missed."""
    async with session_scope() as conn:
        result = await conn.execute(
            text(
                """
                WITH expiring AS (
                    SELECT c.id::text AS id, c.company_id::text AS cid, c.public_id,
                           c.title,
                           c.notice_period_end,
                           (c.end_date - current_date) AS days_left
                      FROM public.contracts c
                     WHERE c.deleted_at IS NULL
                       AND c.status = 'ACTIVE'
                       AND c.end_date IS NOT NULL
                       AND c.end_date <= current_date + interval '60 days'
                ),
                created AS (
                    INSERT INTO public.ai_insights
                      (public_id, company_id, insight_type, severity, title, summary,
                       entity_type, entity_id, is_prediction, required_permission)
                    SELECT 'IN' || upper(substr(md5(random()::text), 1, 6)), e.cid,
                           'RISK',
                           CASE WHEN e.days_left <= 30 THEN 'HIGH' ELSE 'MEDIUM' END,
                           'Contract expiring soon: ' || e.title,
                           format('This contract ends in %s days. Notice period: %s.',
                                  e.days_left, COALESCE(e.notice_period_end::text, 'not set')),
                           'contract', CAST(e.id AS uuid), false, 'contracts.read'
                      FROM expiring e
                     WHERE NOT EXISTS (
                        SELECT 1 FROM public.ai_insights i
                         WHERE i.entity_id = CAST(e.id AS uuid)
                           AND i.insight_type = 'RISK'
                           AND i.dismissed_at IS NULL
                           AND i.created_at > now() - interval '7 days'
                     )
                    RETURNING id
                )
                SELECT count(*)::int AS n FROM created
                """
            )
        )
        count = int(result.scalar() or 0)

    if count:
        logger.info("contract_expiry_insights_created", count=count)
    return {"created": count}


@async_task(name="app.workers.tasks.refresh_dashboard_stats", queue="bulk")
async def refresh_dashboard_stats() -> dict[str, str]:
    async with session_scope() as conn:
        await conn.execute(text("SELECT app.refresh_dashboard_stats()"))
    return {"status": "refreshed"}


@async_task(name="app.workers.tasks.purge_expired_idempotency_keys", queue="bulk")
async def purge_expired_idempotency_keys() -> dict[str, int]:
    async with session_scope() as conn:
        result = await conn.execute(
            text("DELETE FROM platform.idempotency_keys WHERE expires_at < now()")
        )
        removed = result.rowcount or 0
    return {"removed": removed}


@async_task(name="app.workers.tasks.process_document_version", queue="bulk")
async def process_document_version(version_id: str) -> dict[str, Any]:
    """Scan, extract and classify one document version. Idempotent.

    Triggered by DOCUMENT_UPLOADED outbox events and by POST
    /documents/{id}/process. Each stage records its own state, so a retry
    resumes rather than restarts: a CLEAN scan is never repeated.
    """
    import uuid as _uuid

    from app.services import document_pipeline

    async with session_scope() as conn:
        already = (
            await conn.execute(
                text("SELECT scan_status FROM public.document_versions WHERE id = :vid"),
                {"vid": version_id},
            )
        ).scalar_one_or_none()
        if already is None:
            return {"version_id": version_id, "skipped": "missing"}
        result = await document_pipeline.process_version(
            conn, version_id=_uuid.UUID(str(version_id))
        )
    return {"version_id": str(version_id), **result}
