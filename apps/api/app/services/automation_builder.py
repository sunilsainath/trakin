"""Natural-language automation drafts: description -> trigger/condition/action.

The compiler is deliberately deterministic (pattern matching, not generation):
an automation the platform cannot execute must never be invented. Anything the
description does not map to is returned as an unparsed remainder for the human
to resolve, and the draft is stored inactive: nothing runs until reviewed.

Financial and contractual actions always require human approval, whatever the
description asks for.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.errors import ValidationError
from app.services import audit

TRIGGERS: dict[str, dict[str, Any]] = {
    "contract expiring": {"trigger_type": "CONTRACT_EXPIRING", "permission": "contracts.read"},
    "contract expires": {"trigger_type": "CONTRACT_EXPIRING", "permission": "contracts.read"},
    "invoice overdue": {"trigger_type": "INVOICE_OVERDUE", "permission": "invoices.read"},
    "invoice unpaid": {"trigger_type": "INVOICE_OVERDUE", "permission": "invoices.read"},
    "timesheet submitted": {
        "trigger_type": "TIMESHEET_SUBMITTED",
        "permission": "timesheets.read_any",
    },
    "timesheet pending": {
        "trigger_type": "TIMESHEET_SUBMITTED",
        "permission": "timesheets.read_any",
    },
    "payment received": {"trigger_type": "PAYMENT_DETECTED", "permission": "payments.read"},
    "msa expiring": {"trigger_type": "MSA_EXPIRING", "permission": "msas.read"},
    "msa expires": {"trigger_type": "MSA_EXPIRING", "permission": "msas.read"},
}

ACTIONS: dict[str, dict[str, Any]] = {
    "notify": {"action_type": "NOTIFY", "requires_approval": False},
    "send notification": {"action_type": "NOTIFY", "requires_approval": False},
    "alert": {"action_type": "NOTIFY", "requires_approval": False},
    "email": {"action_type": "EMAIL", "requires_approval": False},
    "send email": {"action_type": "EMAIL", "requires_approval": False},
    "create task": {"action_type": "CREATE_TASK", "requires_approval": False},
    "approve": {"action_type": "APPROVE", "requires_approval": True},
    "submit invoice": {"action_type": "SUBMIT_INVOICE", "requires_approval": True},
    "generate invoice": {"action_type": "GENERATE_INVOICE", "requires_approval": True},
    "initiate payment": {"action_type": "INITIATE_PAYMENT", "requires_approval": True},
    "send contract": {"action_type": "SEND_CONTRACT", "requires_approval": True},
}

_DAYS_RE = re.compile(r"within\s+(\d+)\s+days?")
_AMOUNT_RE = re.compile(r"(?:over|above|more than)\s+\$?([\d,]+(?:\.\d+)?)")


def _mentions(text_words: list[str], phrase: str) -> bool:
    """Phrase words present in order, possibly with words between them, so
    'invoice is overdue' still matches the 'invoice overdue' trigger."""
    needed = phrase.split()
    start = -1
    for word in needed:
        found = next((i for i in range(start + 1, len(text_words)) if text_words[i] == word), -1)
        if found < 0:
            return False
        start = found
    return True


def compile_description(description: str) -> dict[str, Any]:
    """Compile a description into a draft structure with an unparsed remainder."""
    lowered = f" {description.lower()} "
    words = re.findall(r"[a-z0-9$]+", lowered)
    trigger = next((spec for phrase, spec in TRIGGERS.items() if _mentions(words, phrase)), None)
    action = next((spec for phrase, spec in ACTIONS.items() if _mentions(words, phrase)), None)

    conditions: dict[str, Any] = {}
    if match := _DAYS_RE.search(lowered):
        conditions["within_days"] = int(match.group(1))
    if match := _AMOUNT_RE.search(lowered):
        conditions["amount_over"] = match.group(1).replace(",", "")

    covered = sorted(
        {phrase for phrase in list(TRIGGERS) + list(ACTIONS) if _mentions(words, phrase)}
    )
    remainder = lowered
    for phrase in sorted(covered, key=len, reverse=True):
        remainder = remainder.replace(phrase, " ")
    remainder = re.sub(r"\s+", " ", remainder).strip(" ,.")

    return {
        "trigger": dict(trigger) if trigger else None,
        "action": dict(action) if action else None,
        "conditions": conditions,
        "unparsed": remainder,
        "complete": trigger is not None and action is not None,
    }


async def draft_automation(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    request_id: str,
    description: str,
    name: str | None = None,
) -> dict[str, Any]:
    """Compile and store an inactive draft. Never activates on creation."""
    if len(description.strip()) < 10:
        raise ValidationError("Describe the workflow in at least a few words.")
    if len(description) > 2000:
        raise ValidationError("Workflow descriptions are limited to 2000 characters.")

    compiled = compile_description(description)
    if compiled["trigger"] is None:
        raise ValidationError(
            "No supported trigger was recognised. Try 'contract expiring', "
            "'invoice overdue', 'timesheet submitted', 'payment received' or 'msa expiring'.",
            details={"unparsed": compiled["unparsed"]},
        )
    if compiled["action"] is None:
        raise ValidationError(
            "No supported action was recognised. Try 'notify', 'email', "
            "'create task', 'approve', 'generate invoice' or 'initiate payment'.",
            details={"unparsed": compiled["unparsed"]},
        )

    row = (
        (
            await conn.execute(
                text(
                    """
                    INSERT INTO public.ai_automations
                      (company_id, name, description, natural_language_prompt,
                       compiled_definition, trigger_type, conditions, actions,
                       required_permission, is_active, requires_human_approval,
                       created_by)
                    VALUES (:cid, :name, :description, :prompt,
                            CAST(:compiled AS jsonb), :trigger,
                            CAST(:conditions AS jsonb), CAST(:actions AS jsonb),
                            :permission, false, :needs_approval, :actor)
                    RETURNING public_id
                    """
                ),
                {
                    "cid": company_id,
                    "name": (name or description[:80]).strip(),
                    "description": description.strip(),
                    "prompt": description.strip(),
                    "compiled": json.dumps(compiled),
                    "trigger": compiled["trigger"]["trigger_type"],
                    "conditions": json.dumps(compiled["conditions"]),
                    "actions": json.dumps([compiled["action"]]),
                    "permission": compiled["trigger"]["permission"],
                    "needs_approval": bool(compiled["action"]["requires_approval"]),
                    "actor": actor_user_id,
                },
            )
        )
        .mappings()
        .first()
    )
    await audit.record(
        conn,
        action="ai.automation_drafted",
        resource_type="ai_automation",
        resource_public_id=str(row["public_id"]),
        company_id=company_id,
        actor_user_id=actor_user_id,
        new_values={
            "trigger": compiled["trigger"]["trigger_type"],
            "complete": compiled["complete"],
            "unparsed": compiled["unparsed"],
        },
        request_id=request_id,
    )
    return {
        "public_id": str(row["public_id"]),
        "trigger": compiled["trigger"],
        "action": compiled["action"],
        "conditions": compiled["conditions"],
        "unparsed": compiled["unparsed"],
        "is_active": False,
        "requires_human_approval": bool(compiled["action"]["requires_approval"]),
        "request_id": request_id,
    }
