"""Processor webhook ingest: signature verify, idempotent persist, outbox fan-out.

Authentication here is the signature itself: these routes carry no user
session, so an unverifiable body is rejected before anything is stored.
Duplicates (same provider + event id) return the original receipt without
re-processing — webhooks are redelivered by every processor.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.config import get_settings
from app.core.errors import ValidationError, WebhookSignatureError
from app.core.logging import get_logger

logger = get_logger(__name__)

_SUPPORTED_PROVIDERS = {"stripe"}


def _expected_signatures(body: bytes, secret: str, provided: dict[str, str]) -> bool:
    """Stripe-style `t=..,v1=..` or plain `sha256=<hex>` HMAC verification."""
    if "v1" in provided and "t" in provided:
        signed = f"{provided['t']}.".encode() + body
        digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
        return hmac.compare_digest(digest, provided["v1"])
    if "sha256" in provided:
        digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(digest, provided["sha256"])
    return False


def _parse_signature_header(value: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in value.split(","):
        if "=" in part:
            key, _, val = part.partition("=")
            out[key.strip()] = val.strip()
    if "=" not in value and value:
        out["sha256"] = value.replace("sha256=", "")
    return out


async def ingest_processor_webhook(
    conn: AsyncConnection,
    *,
    provider: str,
    raw_body: bytes,
    signature: str | None,
    request_id: str,
) -> dict[str, Any]:
    provider = str(provider or "").lower()
    if provider not in _SUPPORTED_PROVIDERS:
        raise ValidationError(f"Unknown webhook provider: {provider}.")

    settings = get_settings()
    secret = settings.payment_processor_webhook_secret.get_secret_value()
    if not secret:
        # Misconfiguration must not silently accept forged webhooks.
        raise ValidationError("Webhook ingest is not configured for this environment.")

    try:
        payload: Any = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ValidationError("Webhook body is not valid JSON.") from None

    event_id = str(payload.get("id") or "")
    if not event_id:
        raise ValidationError("Webhook event carries no id.")
    if not _expected_signatures(raw_body, secret, _parse_signature_header(signature or "")):
        logger.warning("webhook_signature_invalid", provider=provider, event_id=event_id)
        raise WebhookSignatureError()

    payload_hash = hashlib.sha256(raw_body).hexdigest()
    row = (
        (
            await conn.execute(
                text(
                    """
                    INSERT INTO platform.processor_webhook_events
                      (provider, provider_event_id, event_type, signature_verified,
                       payload, payload_hash)
                    VALUES (:provider, :event_id, :event_type, true,
                            CAST(:payload AS jsonb), :payload_hash)
                    ON CONFLICT (provider, provider_event_id) DO NOTHING
                    RETURNING id::text AS id
                    """
                ),
                {
                    "provider": provider,
                    "event_id": event_id,
                    "event_type": str(payload.get("type") or "unknown"),
                    "payload": json.dumps(payload),
                    "payload_hash": payload_hash,
                },
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        # Redelivery: the first receipt stands, nothing re-runs.
        return {"ok": True, "duplicate": True, "request_id": request_id}

    await conn.execute(
        text(
            """
            INSERT INTO platform.outbox_events
              (event_type, event_version, actor_user_id, aggregate_type,
               aggregate_id, payload, idempotency_key)
            VALUES ('PAYMENT_WEBHOOK_RECEIVED', 1, CAST(NULL AS uuid), 'processor_webhook',
                    CAST(:aggregate_id AS uuid),
                    jsonb_build_object(
                      'provider', CAST(:provider AS text),
                      'event_id', CAST(:event_id AS text)),
                    :idem)
            -- Matches ux_outbox_idempotency (event_type, idempotency_key
            -- WHERE NOT NULL); a redelivered webhook is a no-op.
            ON CONFLICT (event_type, idempotency_key)
              WHERE idempotency_key IS NOT NULL DO NOTHING
            """
        ),
        {
            "aggregate_id": row["id"],
            "provider": provider,
            "event_id": event_id,
            "idem": f"webhook:{provider}:{event_id}",
        },
    )
    logger.info("webhook_ingested", provider=provider, event_id=event_id)
    return {"ok": True, "duplicate": False, "request_id": request_id}
