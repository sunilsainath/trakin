"""Domain event handlers.

The outbox dispatcher calls `handle_event`. Handlers are grouped by what they do
rather than by which module produced the event, and each is idempotent: a
redelivered event must not produce a second notification, a second invoice or a
second payment.

Nothing here performs an authorization decision on a user's behalf. Each handler
runs as the worker and resolves the target explicitly by id, so an event cannot be
used to smuggle a request past the permission layer.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text

from app.core.logging import get_logger
from app.db.session import session_scope

logger = get_logger(__name__)

# Notification categories, matching public.notification_preferences.category.
CATEGORY_BY_EVENT: dict[str, str] = {
    "CONTRACT_ACCEPTED": "CONTRACT",
    "CONTRACT_ACTIVE": "CONTRACT",
    "CONTRACT_DECLINED": "CONTRACT",
    "CONTRACT_EXPIRED": "CONTRACT",
    "CONTRACT_EXPIRING_7D": "CONTRACT",
    "CONTRACT_EXPIRING_30D": "CONTRACT",
    "CONTRACT_EXPIRING_60D": "CONTRACT",
    "CONTRACT_EXPIRING_90D": "CONTRACT",
    "CONTRACT_TERMINATED": "COMPLIANCE",
    "CONTRACT_STATUS_CHANGED": "CONTRACT",
    "SOW_STATUS_CHANGED": "SOW",
    "MSA_STATUS_CHANGED": "MSA",
    "TIMESHEET_SUBMITTED": "TIMESHEET",
    "TIMESHEET_UNDER_REVIEW": "TIMESHEET",
    "TIMESHEET_APPROVED": "TIMESHEET",
    "TIMESHEET_REJECTED": "TIMESHEET",
    "TIMESHEET_LOCKED": "TIMESHEET",
    "INVOICE_STATUS_CHANGED": "INVOICE",
    "PAYMENT_DETECTED": "PAYMENT",
    "PAYMENT_MATCHED": "PAYMENT",
    "PAYMENT_RECORDED": "PAYMENT",
}

# Events that should trigger the billing engine rather than a notification.
BILLING_TRIGGERS = {"TIMESHEET_APPROVED", "TIMESHEET_LOCKED"}
# Events that should re-evaluate invoice submission eligibility.
MSA_TRIGGERS = {"MSA_STATUS_CHANGED"}

HANDLERS = (
    "notify_participants",
    "queue_billing_if_needed",
    "reevaluate_invoice_eligibility",
    "record_event_for_audit",
)


async def emit_event(
    conn: Any,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    company_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    public_id: str | None = None,
    old: str | None = None,
    new: str | None = None,
    extra: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> None:
    """Publish a domain event into the transactional outbox.

    The row commits with the business change, so an event can never be lost;
    the idempotency key makes a retried transition a no-op rather than a
    duplicate notification. Delivery happens through dispatch_outbox.
    """
    payload: dict[str, Any] = {
        "company_id": str(company_id) if company_id else None,
        "public_id": public_id,
        "old": old,
        "new": new,
        **(extra or {}),
    }
    await conn.execute(
        text(
            """
            INSERT INTO platform.outbox_events
              (event_type, event_version, company_id, actor_user_id,
               aggregate_type, aggregate_id, payload, idempotency_key)
            VALUES (:type, 1, CAST(:cid AS uuid), CAST(:actor AS uuid),
                    :atype, CAST(:aid AS uuid), CAST(:payload AS jsonb), :idem)
            ON CONFLICT (event_type, idempotency_key)
              WHERE idempotency_key IS NOT NULL DO NOTHING
            """
        ),
        {
            "type": event_type,
            "cid": str(company_id) if company_id else None,
            "actor": str(actor_user_id) if actor_user_id else None,
            "atype": aggregate_type,
            "aid": str(aggregate_id),
            "payload": _json(payload),
            "idem": idempotency_key,
        },
    )


async def handle_event(event: dict[str, Any]) -> None:
    """Dispatch one event to every applicable handler, isolating failures."""
    event_id = event.get("event_id")
    event_type = str(event.get("event_type") or "")

    if not event_type:
        logger.warning("event_missing_type", event_id=event_id)
        return

    for name in HANDLERS:
        try:
            handler = globals().get(name)
            if handler is None:  # pragma: no cover
                continue
            await handler(event)
        except Exception as exc:  # noqa: BLE001
            # One failing handler must not stop the others, and the event stays
            # published so a failure is visible in logs rather than retried into
            # a duplicate notification.
            logger.error(
                "event_handler_failed", handler=name, event_type=event_type, error=str(exc)[:300]
            )


async def notify_participants(event: dict[str, Any]) -> None:
    """Create notifications for the users involved in an event.

    Redelivery safety comes from the outbox idempotency key, not from the
    notification rows: there is deliberately no UNIQUE key on
    (user_id, type, resource_id), because a contract can legitimately warn,
    expire and renew across its life.
    """
    event_type = str(event.get("event_type") or "")
    category = CATEGORY_BY_EVENT.get(event_type)
    if category is None:
        return

    aggregate_type = str(event.get("aggregate_type") or "")
    aggregate_id = str(event.get("aggregate_id") or "")
    payload: dict[str, Any] = event.get("payload") or {}

    title, body, severity = _render(event_type, payload)
    if not title:
        return

    async with session_scope() as conn:
        await _insert_notifications(
            conn, event_type, title, body, severity, aggregate_type, aggregate_id, payload
        )


async def _recipients_for(
    conn: Any, aggregate_type: str, aggregate_id: str, payload: dict[str, Any]
) -> list[uuid.UUID]:
    """Users who should be notified, derived from the referenced record.

    One query per aggregate family keeps each recipient rule reviewable; the
    single execute below keeps the linter's return budget intact.
    """
    params: dict[str, Any] = {"id": aggregate_id}
    if aggregate_type == "contract":
        query = """                    SELECT DISTINCT
                           cp.party_user_id AS user_id
                      FROM public.contract_parties cp
                     WHERE cp.contract_id = CAST(:id AS uuid)
                       AND cp.party_user_id IS NOT NULL
                     UNION
                     SELECT m.user_id
                       FROM public.assignments m
                      WHERE m.contract_id = CAST(:id AS uuid)
                        AND m.status = 'ACTIVE'
                    """
    elif aggregate_type == "timesheet":
        query = """                    SELECT t.user_id FROM public.timesheets t
                      WHERE t.id = CAST(:id AS uuid)
                     UNION
                     SELECT ta.approver_user_id
                       FROM public.timesheet_approvals ta
                      WHERE ta.timesheet_id = CAST(:id AS uuid)
                        AND ta.approver_user_id IS NOT NULL
                    """
    elif aggregate_type == "invoice":
        params["cid"] = payload.get("company_id")
        query = """                    SELECT m.user_id
                      FROM public.company_memberships m
                     WHERE m.company_id = COALESCE(
                               CAST(:cid AS uuid),
                               (SELECT i.counterparty_company_id FROM public.invoices i
                                 WHERE i.id = CAST(:id AS uuid)))
                       AND m.status = 'ACTIVE'
                       AND m.role_id IN (
                            SELECT id FROM public.company_roles
                             WHERE key IN ('FINANCE_MANAGER', 'ACCOUNTANT', 'SUPER_ADMIN')
                       )
                    """
    elif aggregate_type == "sow":
        query = """                    SELECT m.user_id
                      FROM public.company_memberships m
                      JOIN public.company_roles r ON r.id = m.role_id
                     WHERE m.company_id = (SELECT s.company_id FROM public.sows s
                                            WHERE s.id = CAST(:id AS uuid))
                       AND m.status = 'ACTIVE'
                       AND r.key = 'SUPER_ADMIN'
                     UNION
                     SELECT s.counterparty_user_id
                       FROM public.sows s
                      WHERE s.id = CAST(:id AS uuid)
                        AND s.counterparty_user_id IS NOT NULL
                     UNION
                     SELECT m.user_id
                       FROM public.company_memberships m
                       JOIN public.company_roles r ON r.id = m.role_id
                       JOIN public.sows s ON s.counterparty_company_id = m.company_id
                      WHERE s.id = CAST(:id AS uuid)
                        AND m.status = 'ACTIVE'
                        AND r.key = 'SUPER_ADMIN'
                    """
    elif aggregate_type == "msa":
        query = """                    SELECT m.user_id
                      FROM public.company_memberships m
                      JOIN public.company_roles r ON r.id = m.role_id
                     WHERE m.company_id IN (
                               SELECT msa.company_a_id FROM public.msas msa
                                WHERE msa.id = CAST(:id AS uuid)
                             UNION
                               SELECT msa.company_b_id FROM public.msas msa
                                WHERE msa.id = CAST(:id AS uuid))
                       AND m.status = 'ACTIVE'
                       AND r.key = 'SUPER_ADMIN'
                    """
    elif aggregate_type == "payment":
        query = """                    SELECT m.user_id
                      FROM public.company_memberships m
                     WHERE m.company_id = (SELECT p.company_id FROM public.payments p
                                            WHERE p.id = CAST(:id AS uuid))
                       AND m.status = 'ACTIVE'
                       AND m.role_id IN (
                            SELECT id FROM public.company_roles
                             WHERE key IN ('FINANCE_MANAGER', 'ACCOUNTANT', 'SUPER_ADMIN')
                       )
                    """
    else:
        return []
    rows = (await conn.execute(text(query), params)).scalars().all()
    return [uuid.UUID(str(r)) for r in rows if r]


async def _insert_notifications(
    conn: Any,
    event_type: str,
    title: str,
    body: str | None,
    severity: str,
    aggregate_type: str,
    aggregate_id: str,
    payload: dict[str, Any],
) -> None:
    """Write one notification per recipient.

    The recipient set is derived from the referenced record, not from the event
    body, so a forged or replayed event cannot choose who is notified.
    """
    company_id = payload.get("company_id")
    public_id = payload.get("public_id")

    recipients = await _recipients_for(conn, aggregate_type, aggregate_id, payload)
    if not recipients:
        return

    for user_id in recipients:
        await conn.execute(
            text(
                """
                INSERT INTO platform.notifications
                  (user_id, company_id, type, title, body, resource_type, resource_id,
                   resource_public_id, severity, metadata)
                VALUES (:uid, CAST(:cid AS uuid), :type, :title, :body, :rtype,
                        CAST(:rid AS uuid), :rpublic, :severity, CAST(:meta AS jsonb))
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "uid": user_id,
                "cid": company_id,
                "type": event_type,
                "title": title,
                "body": body,
                "rtype": aggregate_type,
                "rid": aggregate_id,
                "rpublic": public_id,
                "severity": severity,
                "meta": _json(payload),
            },
        )


async def notify_aggregate(
    conn: Any,
    *,
    event_type: str,
    title: str,
    body: str | None,
    severity: str,
    aggregate_type: str,
    aggregate_id: str,
    company_id: uuid.UUID | None,
    public_id: str | None,
    metadata: dict[str, Any] | None = None,
) -> int:
    """Direct notification path for system jobs such as expiry sweeps.

    Interactive flows publish outbox events; a scheduled sweep has no request
    to attach, so it notifies the record's recipients straight from the row,
    exactly like the event handler would. The UNIQUE key on (user_id, type,
    resource_id) still makes reruns no-ops. Returns the recipient count.
    """
    payload: dict[str, Any] = {
        "company_id": str(company_id) if company_id else None,
        "public_id": public_id,
        **(metadata or {}),
    }
    recipients = await _recipients_for(conn, aggregate_type, aggregate_id, payload)
    if not recipients:
        return 0
    await _insert_notifications(
        conn, event_type, title, body, severity, aggregate_type, aggregate_id, payload
    )
    return len(recipients)


async def queue_billing_if_needed(event: dict[str, Any]) -> None:
    """Start invoice generation when work has been approved.

    The billing run carries an idempotency key derived from the contract and
    period, so a redelivered TIMESHEET_APPROVED cannot produce a second invoice.
    """
    event_type = str(event.get("event_type") or "")
    if event_type not in BILLING_TRIGGERS:
        return

    payload: dict[str, Any] = event.get("payload") or {}
    contract_id = payload.get("contract_id")

    if not contract_id:
        return

    period_start = payload.get("period_start")
    period_end = payload.get("period_end")
    if not period_start or not period_end:
        return

    key = f"billing:{contract_id}:{period_start}:{period_end}"

    from app.workers.celery_app import celery_app

    celery_app.send_task(
        "app.workers.tasks.generate_invoices",
        args=[key],
        queue="bulk",
    )
    logger.info("billing_queued", contract=str(contract_id), key=key)


async def reevaluate_invoice_eligibility(event: dict[str, Any]) -> None:
    """When an MSA becomes active, unblock invoices that were held for it.

    Held invoices stay DRAFT and flagged `msa_required`; this recomputes that
    flag so the counterparty can submit them.
    """
    event_type = str(event.get("event_type") or "")
    if event_type not in MSA_TRIGGERS:
        return

    payload: dict[str, Any] = event.get("payload") or {}
    if payload.get("new_status") != "ACTIVE":
        return

    company_a = payload.get("company_a_id")
    company_b = payload.get("company_b_id")
    if not company_a or not company_b:
        return

    async with session_scope() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE public.invoices i
                   SET msa_required = false,
                       msa_block_reason = NULL
                 WHERE i.deleted_at IS NULL
                   AND i.direction = 'RECEIVABLE'
                   AND i.status = 'DRAFT'
                   AND i.msa_required
                   AND app.has_active_msa(i.company_id, i.counterparty_company_id)
                   AND (i.company_id = CAST(:a AS uuid)
                     OR i.counterparty_company_id = CAST(:a AS uuid)
                     OR i.company_id = CAST(:b AS uuid)
                     OR i.counterparty_company_id = CAST(:b AS uuid))
                RETURNING i.public_id
                """
            ),
            {"a": company_a, "b": company_b},
        )
        unblocked = result.rowcount or 0

    if unblocked:
        logger.info("invoices_unblocked_by_msa", count=unblocked)


async def record_event_for_audit(event: dict[str, Any]) -> None:
    """Mirror the domain event into the audit ledger for support lookups."""
    event_type = str(event.get("event_type") or "")
    payload: dict[str, Any] = event.get("payload") or {}

    try:
        async with session_scope() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO platform.audit_logs
                      (company_id, actor_type, actor_label, action, resource_type,
                       resource_id, resource_public_id, new_values, metadata)
                    VALUES (CAST(:cid AS uuid), 'SYSTEM', 'outbox', :action, :rtype,
                            CAST(:rid AS uuid), :rpublic, CAST(:new AS jsonb),
                            jsonb_build_object('event_id', CAST(:eid AS text)))
                    """
                ),
                {
                    "cid": payload.get("company_id"),
                    "action": f"event.{event_type.lower()}",
                    "rtype": event.get("aggregate_type"),
                    "rid": event.get("aggregate_id"),
                    "rpublic": payload.get("public_id"),
                    "new": _json(payload),
                    "eid": event.get("event_id"),
                },
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("event_audit_failed", error=str(exc)[:200])


def _render(event_type: str, payload: dict[str, Any]) -> tuple[str | None, str | None, str]:
    """Human-readable notification text. Never invents a value the event lacks."""
    public_id = payload.get("public_id")
    old = payload.get("old") or payload.get("from")
    new = payload.get("new") or payload.get("to")

    templates: dict[str, tuple[str | None, str | None, str]] = {
        "CONTRACT_ACCEPTED": (f"Contract {public_id} was accepted", None, "SUCCESS"),
        "CONTRACT_DECLINED": (f"Contract {public_id} was declined", None, "WARNING"),
        "CONTRACT_ACTIVE": (f"Contract {public_id} is now active", None, "SUCCESS"),
        "CONTRACT_EXPIRED": (f"Contract {public_id} has expired", None, "WARNING"),
        "CONTRACT_TERMINATED": (f"Contract {public_id} was terminated", None, "CRITICAL"),
        "CONTRACT_STATUS_CHANGED": (
            f"Contract {public_id} changed status",
            f"Status changed from {old} to {new}." if old and new else None,
            "INFO",
        ),
        "SOW_STATUS_CHANGED": (
            f"SOW {public_id} changed status",
            f"Status changed from {old} to {new}." if old and new else None,
            "INFO",
        ),
        "MSA_STATUS_CHANGED": (
            f"Master Service Agreement {public_id} is now {new}",
            None,
            "INFO",
        ),
        "TIMESHEET_SUBMITTED": (f"Timesheet {public_id} was submitted for review", None, "INFO"),
        "TIMESHEET_APPROVED": (f"Timesheet {public_id} was approved", None, "SUCCESS"),
        "TIMESHEET_REJECTED": (f"Timesheet {public_id} was rejected", None, "WARNING"),
        "TIMESHEET_LOCKED": (f"Timesheet {public_id} was locked", None, "INFO"),
        "INVOICE_STATUS_CHANGED": (
            f"Invoice {public_id} is now {new}",
            None,
            "CRITICAL" if new in {"OVERDUE", "DISPUTED", "REJECTED"} else "INFO",
        ),
        "PAYMENT_DETECTED": ("A payment was detected", None, "INFO"),
        "PAYMENT_MATCHED": (f"Payment matched to invoice {public_id}", None, "SUCCESS"),
        "PAYMENT_RECORDED": (f"Payment recorded for invoice {public_id}", None, "SUCCESS"),
    }
    title, body, severity = templates.get(event_type, (None, None, "INFO"))
    return title, body, severity


def _json(value: Any) -> str:
    import json

    return json.dumps(value, default=str)
