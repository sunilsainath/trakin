"""Audit trail writer.

Every sensitive operation records who did what, to which resource, from where.
The insert happens inside the caller's transaction, so an audit record cannot
exist without the change it describes, and a change cannot commit without its
record.

Values are redacted before insert: a password, token or full TIN never reaches
this table, which is readable by anyone holding `audit.read`.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.logging import REDACTED, get_logger, scrub

logger = get_logger(__name__)

ActorType = Literal["USER", "SYSTEM", "AI", "AGENT", "INTEGRATION"]

# Never recorded, whatever the caller's diff says.
_ALWAYS_REDACTED = frozenset(
    {
        "password",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "secret",
        "ssn",
        "tax_id",
        "tin",
        "account_number",
        "routing_number",
        "card_number",
        "iban",
        "ssn_encrypted",
        "tax_id_encrypted",
        "access_token_encrypted",
        "item_id_encrypted",
    }
)


def _redact_diff(values: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if values is None:
        return None
    out: dict[str, Any] = {}
    for key, value in values.items():
        if key.lower() in _ALWAYS_REDACTED:
            out[key] = REDACTED
        else:
            out[key] = scrub(value)
    return out


def _changed_fields(old: Mapping[str, Any] | None, new: Mapping[str, Any] | None) -> list[str]:
    if not old or not new:
        return []
    return sorted(
        key
        for key in set(old) | set(new)
        if old.get(key) != new.get(key) and key.lower() not in _ALWAYS_REDACTED
    )


async def record(
    conn: AsyncConnection,
    *,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID | None = None,
    resource_public_id: str | None = None,
    company_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    actor_type: ActorType = "USER",
    actor_label: str | None = None,
    old_values: Mapping[str, Any] | None = None,
    new_values: Mapping[str, Any] | None = None,
    reason: str | None = None,
    request_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> None:
    """Append one immutable audit record."""
    old_clean = _redact_diff(old_values)
    new_clean = _redact_diff(new_values)

    await conn.execute(
        text(
            """
            INSERT INTO platform.audit_logs
              (company_id, actor_user_id, actor_type, actor_label, action,
               resource_type, resource_id, resource_public_id,
               old_values, new_values, changed_fields, reason,
               request_id, ip_address, user_agent, metadata)
            VALUES
              (:company_id, :actor_user_id, :actor_type, :actor_label, :action,
               :resource_type, :resource_id, :resource_public_id,
               CAST(:old_values AS jsonb), CAST(:new_values AS jsonb),
               CAST(:changed_fields AS text[]), :reason,
               :request_id, :ip_address, :user_agent, CAST(:metadata AS jsonb))
            """
        ),
        {
            "company_id": company_id,
            "actor_user_id": actor_user_id,
            "actor_type": actor_type,
            "actor_label": actor_label,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "resource_public_id": resource_public_id,
            "old_values": _json(old_clean),
            "new_values": _json(new_clean),
            "changed_fields": _changed_fields(old_clean, new_clean),
            "reason": reason,
            "request_id": request_id,
            # Cast to ::inet only when it parses; a spoofable header must not
            # break the write.
            "ip_address": _safe_ip(ip_address),
            "user_agent": (user_agent or "")[:500] or None,
            "metadata": _json(scrub(dict(metadata or {}))),
        },
    )

    logger.debug(
        "audit_recorded",
        action=action,
        resource_type=resource_type,
        resource_public_id=resource_public_id,
    )


def _json(value: Any) -> str:
    import json

    return json.dumps(value, default=str)


def _safe_ip(value: str | None) -> str | None:
    import ipaddress

    if not value:
        return None
    try:
        return str(ipaddress.ip_address(value.split(",")[0].strip()))
    except ValueError:
        # An unparseable value means a proxy chain or a spoofed header; storing
        # it as ::inet would raise, so it is dropped rather than guessed.
        return None


async def record_many(conn: AsyncConnection, events: Iterable[dict[str, Any]]) -> None:
    for event in events:
        await record(conn, **event)  # type: ignore[arg-type]
