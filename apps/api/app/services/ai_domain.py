"""AI domain intelligence.

Every function here follows three rules:

  1. **Authorization first.** Data is gathered with the same company predicate
     and the same permission checks the ordinary endpoints use. The AI never sees
     a row a signed-in user could not open (rule 9).
  2. **Deterministic before generative.** Rule checks, anomaly detection and
     scoring are computed in SQL/Python, so they are reproducible and testable.
     The model is used for explanation, not for the verdict.
  3. **Degrade honestly.** With no provider configured, the endpoint returns the
     deterministic findings and `provider: null`. It never invents an answer.

Reuses the existing gateway (`app.ai.gateway`), agent registry and RAG
retrieval; no new provider abstraction is introduced.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.ai.agents import get_agents
from app.ai.gateway import ChatRequest, Message, get_gateway, wrap_untrusted
from app.core.clock import utc_today
from app.core.errors import IntegrationNotConfiguredError
from app.core.logging import get_logger
from app.services.invoicing import money
from app.services.lookup import as_decimal, resolve_scoped

logger = get_logger(__name__)

ZERO = Decimal("0")

Finding = dict[str, Any]


# =============================================================================
# provider availability
# =============================================================================
def provider_status() -> dict[str, Any]:
    """Which AI capabilities are live right now, and why not when they are not."""
    gateway = get_gateway()
    capabilities = gateway.registry.capabilities()
    flags = {flag["key"]: flag for flag in capabilities.get("flags", [])}
    return {
        "configured": bool(gateway.registry.available_chat_keys()),
        "available_providers": gateway.registry.available_chat_keys(),
        "chat": capabilities.get("chat"),
        "embeddings": capabilities.get("embeddings"),
        "routing": capabilities.get("routing"),
        "flags": flags,
    }


async def _chat(
    *,
    company_id: uuid.UUID,
    user_id: uuid.UUID,
    feature: str,
    system: str,
    context: str,
    question: str,
) -> dict[str, Any]:
    """One guarded model call. Returns `provider: None` when unavailable."""
    gateway = get_gateway()
    try:
        response = await gateway.chat(
            ChatRequest(
                messages=[
                    Message(role="system", content=system),
                    Message(
                        role="user",
                        content=f"{wrap_untrusted(context, label='business-data')}\n\n{question}",
                    ),
                ],
                feature=feature,
                company_id=company_id,
                user_id=user_id,
                max_tokens=1200,
                temperature=0.1,
            )
        )
    except IntegrationNotConfiguredError:
        return {"provider": None, "text": None, "reason": "no_provider_configured"}
    except Exception as exc:  # noqa: BLE001 - surfaced as a degraded result
        logger.warning("ai_domain_call_failed", feature=feature, error=str(exc)[:200])
        return {"provider": None, "text": None, "reason": "provider_error"}

    return {
        "provider": response.provider_key,
        "model": response.model,
        "text": response.content,
        "citations": [
            c.model_dump() if hasattr(c, "model_dump") else c for c in response.citations
        ],
        "tokens": response.prompt_tokens + response.completion_tokens,
        "cost_cents": response.cost_cents,
    }


def _parse_json_object(text_value: str | None) -> dict[str, Any]:
    """Models wrap JSON in prose or fences. This extracts the first object."""
    if not text_value:
        return {}
    candidate = text_value.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        _, _, candidate = candidate.partition("\n")
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        parsed = json.loads(candidate[start : end + 1])
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


# =============================================================================
# contract intelligence (§25)
# =============================================================================
EXTRACTION_SYSTEM = """You extract structured facts from a business contract.
Return ONLY a JSON object with these keys, using null when a value is absent:
parties (array of strings), contract_value (number or null), currency (string or null),
effective_date (ISO date or null), end_date (ISO date or null),
renewal_terms (string or null), payment_terms_days (number or null),
notice_period_days (number or null), rates (array of {role, rate, currency}),
roles (array of strings), termination_clauses (array of strings),
obligations (array of {party, obligation}), risks (array of strings),
missing_information (array of strings).
Do not guess. If the document does not state a value, use null or an empty array."""


async def extract_contract_facts(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    user_id: uuid.UUID,
    contract_public_id: str | None = None,
    document_public_id: str | None = None,
    text_value: str | None = None,
) -> dict[str, Any]:
    """Extract commercial terms from contract text with citations."""
    source = ""
    citations: list[Any] = []
    document_ref = document_public_id

    if document_public_id:
        document = await resolve_scoped(conn, "documents", document_public_id, company_id)
        source = await _document_text(conn, document["id"])
        citations = [{"document_public_id": document_public_id, "title": document["title"]}]
        document_ref = document_public_id
    elif contract_public_id:
        contract = await resolve_scoped(conn, "contracts", contract_public_id, company_id)
        source = await _contract_transcript(conn, contract["id"])
        document_ref = contract.get("document_id")
    elif text_value:
        source = text_value
    else:
        from app.core.errors import ValidationError

        raise ValidationError(
            "Provide a contract, a document, or text to analyse.",
            details={"field": "contract_id|document_id|text"},
        )

    deterministic = await _deterministic_contract_facts(
        conn, company_id=company_id, contract_public_id=contract_public_id
    )

    result = await _chat(
        company_id=company_id,
        user_id=user_id,
        feature="ai:contract_intelligence",
        system=EXTRACTION_SYSTEM,
        context=f"CONTRACT DOCUMENT\n{source}",
        question="Extract the structured contract facts as JSON.",
    )

    extracted = _parse_json_object(result.get("text"))
    return {
        "source": {
            "contract_id": contract_public_id,
            "document_public_id": document_ref,
            "characters": len(source),
        },
        "deterministic": deterministic,
        "extracted": extracted,
        "provider": result.get("provider"),
        "model": result.get("model"),
        "citations": citations or result.get("citations", []),
        "degraded_reason": result.get("reason"),
    }


async def _document_text(conn: AsyncConnection, document_id: uuid.UUID) -> str:
    """Plain-text body of a stored document, from the AI chunk index.

    The index is written at upload time; a document with no chunks has no
    extractable text and the caller is told so rather than given an empty answer.
    """
    row = (
        (
            await conn.execute(
                text(
                    """
                    SELECT string_agg(c.content, E'\n' ORDER BY c.chunk_index) AS body,
                           count(*) AS chunks
                      FROM public.ai_document_chunks c
                      JOIN public.ai_knowledge_documents k ON k.id = c.knowledge_document_id
                     WHERE k.document_id = CAST(:did AS uuid)
                    """
                ),
                {"did": document_id},
            )
        )
        .mappings()
        .first()
    )
    return str(row["body"] or "") if row else ""


async def _contract_transcript(conn: AsyncConnection, contract_id: uuid.UUID) -> str:
    """A structured rendering of the contract, so extraction has the real terms."""
    rows = (
        (
            await conn.execute(
                text(
                    """
                    SELECT c.title, c.status, c.currency, c.billing_basis,
                           c.payment_terms_days, c.start_date, c.end_date,
                           c.auto_renew, c.renewal_notice_days, c.termination_notice_days,
                           c.governing_law, c.confidentiality_level, c.metadata,
                           p.name AS project_name, s.title AS sow_title,
                           COALESCE(cp.display_name, cp.legal_name) AS counterparty
                      FROM public.contracts c
                      JOIN public.projects p ON p.id = c.project_id
                      JOIN public.sows s ON s.id = c.sow_id
                      LEFT JOIN public.companies cp ON cp.id = c.counterparty_company_id
                     WHERE c.id = :cid
                    """
                ),
                {"cid": contract_id},
            )
        )
        .mappings()
        .first()
    )
    if rows is None:
        return ""
    parts = [
        f"Title: {rows['title']}",
        f"Project: {rows['project_name']}",
        f"SOW: {rows['sow_title']}",
        f"Counterparty: {rows['counterparty'] or 'n/a'}",
        f"Status: {rows['status']}",
        f"Currency: {rows['currency']}",
        f"Billing basis: {rows['billing_basis']}",
        f"Payment terms (days): {rows['payment_terms_days']}",
        f"Start: {rows['start_date']}",
        f"End: {rows['end_date']}",
        f"Auto renew: {rows['auto_renew']}",
        f"Renewal notice: {rows['renewal_notice_days']}",
        f"Termination notice: {rows['termination_notice_days']}",
        f"Governing law: {rows['governing_law'] or 'n/a'}",
    ]

    roles = await conn.execute(
        text(
            """
            SELECT pr.title, cr.rate, cr.rate_type, cr.currency, cr.quantity,
                   cr.billing_basis, cr.payment_terms_days, cr.max_units,
                   cr.start_date, cr.end_date, cr.overtime_rule, cr.tax_rule
              FROM public.contract_roles cr
              JOIN public.project_roles pr ON pr.id = cr.project_role_id
             WHERE cr.contract_id = :cid ORDER BY pr.title
            """
        ),
        {"cid": contract_id},
    )
    parts.append("Contract roles:")
    for role in roles.mappings().all():
        parts.append(
            f"- {role['title']}: {role['quantity']} x {role['rate']} {role['currency']} "
            f"({role['rate_type']}, {role['billing_basis']}, terms {role['payment_terms_days']}d, "
            f"max {role['max_units']}, {role['start_date']}..{role['end_date']}, "
            f"overtime {role['overtime_rule']}, tax {role['tax_rule']})"
        )

    lines = await conn.execute(
        text(
            """
            SELECT label, description, line_type, quantity, unit, unit_rate, amount,
                   currency, tax_rate
              FROM public.contract_line_items
             WHERE contract_id = :cid AND is_active ORDER BY sort_order
            """
        ),
        {"cid": contract_id},
    )
    parts.append("Line items:")
    for line in lines.mappings().all():
        parts.append(
            f"- {line['label']} ({line['line_type']}): {line['quantity']} {line['unit']} "
            f"x {line['unit_rate']} {line['currency']} = {line['amount']}"
            + (f" + {line['tax_rate']} tax" if as_decimal(line["tax_rate"]) else "")
        )
    return "\n".join(parts)


async def _deterministic_contract_facts(
    conn: AsyncConnection, *, company_id: uuid.UUID, contract_public_id: str | None
) -> dict[str, Any]:
    """Facts read straight from our own records, for comparison with extraction."""
    if not contract_public_id:
        return {}
    contract = await resolve_scoped(conn, "contracts", contract_public_id, company_id)
    metadata = contract.get("metadata") or {}
    roles = await conn.execute(
        text(
            """
            SELECT pr.public_id, pr.title, cr.rate, cr.currency, cr.rate_type,
                   cr.payment_terms_days, cr.max_units, cr.start_date, cr.end_date,
                   cr.overtime_rule, cr.tax_rule, cr.tax_rate
              FROM public.contract_roles cr
              JOIN public.project_roles pr ON pr.id = cr.project_role_id
             WHERE cr.contract_id = :cid ORDER BY pr.title
            """
        ),
        {"cid": contract["id"]},
    )
    return {
        "title": contract["title"],
        "currency": contract["currency"],
        "payment_terms_days": contract["payment_terms_days"],
        "start_date": contract["start_date"],
        "end_date": contract["end_date"],
        "auto_renew": contract["auto_renew"],
        "termination_notice_days": contract["termination_notice_days"],
        "contract_value": metadata.get("contract_value"),
        "roles": [dict(r) for r in roles.mappings().all()],
    }


# =============================================================================
# SOW intelligence (§26)
# =============================================================================
SOW_SYSTEM = """You compare a Statement of Work against its contract and report gaps.
Return ONLY a JSON object:
{"missing_deliverables": [], "extra_deliverables": [], "scope_mismatch": [],
 "financial_mismatch": [], "date_mismatch": [], "role_mismatch": [],
 "payment_term_mismatch": [], "summary": "one paragraph"}.
Only report a difference the supplied data actually shows. Empty arrays when they align."""


async def compare_sow_contract(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    user_id: uuid.UUID,
    sow_public_id: str,
) -> dict[str, Any]:
    sow = await resolve_scoped(conn, "sows", sow_public_id, company_id)
    contracts = (
        (
            await conn.execute(
                text(
                    """
                    SELECT public_id, title, status, currency, billing_basis,
                           payment_terms_days, start_date, end_date
                      FROM public.contracts
                     WHERE sow_id = CAST(:sid AS uuid) AND deleted_at IS NULL
                       AND status <> 'DECLINED'
                     ORDER BY created_at DESC
                    """
                ),
                {"sid": sow["id"]},
            )
        )
        .mappings()
        .all()
    )
    if not contracts:
        from app.core.errors import BusinessRuleViolationError

        raise BusinessRuleViolationError(
            "This SOW has no contract to compare against yet.",
            details={"reason": "SOW_HAS_NO_CONTRACT"},
        )
    contract = dict(contracts[0])

    sow_roles = await conn.execute(
        text(
            """
            SELECT pr.public_id, pr.title, sr.quantity, sr.rate, sr.rate_type, sr.currency
              FROM public.sow_roles sr
              JOIN public.project_roles pr ON pr.id = sr.project_role_id
             WHERE sr.sow_id = :sid ORDER BY pr.title
            """
        ),
        {"sid": sow["id"]},
    )
    contract_roles = await conn.execute(
        text(
            """
            SELECT pr.public_id, pr.title, cr.quantity, cr.rate, cr.rate_type, cr.currency
              FROM public.contract_roles cr
              JOIN public.project_roles pr ON pr.id = cr.project_role_id
             WHERE cr.contract_id = :cid ORDER BY pr.title
            """
        ),
        {
            "cid": contract["id"]
            if "id" in contract
            else _contract_id(conn, company_id, contract["public_id"])
        },
    )

    sow_roles_list: list[dict[str, Any]] = [dict(r) for r in sow_roles.mappings().all()]
    contract_roles_list: list[dict[str, Any]] = [dict(r) for r in contract_roles.mappings().all()]

    deterministic = {
        "roles_only_in_sow": [
            r["title"]
            for r in sow_roles_list
            if r["public_id"] not in {c["public_id"] for c in contract_roles_list}
        ],
        "roles_only_in_contract": [
            r["title"]
            for r in contract_roles_list
            if r["public_id"] not in {s["public_id"] for s in sow_roles_list}
        ],
        "rate_differences": [
            {
                "role": s["title"],
                "sow_rate": str(s["rate"]) if s["rate"] is not None else None,
                "contract_rate": str(c["rate"]) if c["rate"] is not None else None,
            }
            for s in sow_roles_list
            for c in contract_roles_list
            if s["public_id"] == c["public_id"] and as_decimal(s["rate"]) != as_decimal(c["rate"])
        ],
        "payment_terms": {
            "sow": sow["payment_terms_days"],
            "contract": contract["payment_terms_days"],
        },
        "dates": {
            "sow": {"start": sow["start_date"], "end": sow["end_date"]},
            "contract": {"start": contract["start_date"], "end": contract["end_date"]},
        },
        "currency": {"sow": sow["currency"], "contract": contract["currency"]},
    }

    context = (
        f"SOW {sow_public_id}\n"
        f"title: {sow['title']}\n"
        f"scope: {(sow.get('description') or 'n/a')[:4000]}\n"
        f"deliverables: {json.dumps(sow.get('deliverables') or [], default=str)[:3000]}\n"
        f"milestones: {json.dumps(sow.get('milestones') or [], default=str)[:2000]}\n"
        f"max_total_amount: {sow.get('max_total_amount')}\n"
        f"roles: {json.dumps(sow_roles_list, default=str)}\n\n"
        f"CONTRACT {contract['public_id']}\n"
        f"title: {contract['title']}\n"
        f"payment_terms_days: {contract['payment_terms_days']}\n"
        f"billing_basis: {contract['billing_basis']}\n"
        f"roles: {json.dumps(contract_roles_list, default=str)}"
    )

    result = await _chat(
        company_id=company_id,
        user_id=user_id,
        feature="ai:sow_intelligence",
        system=SOW_SYSTEM,
        context=context,
        question="Compare the SOW with the contract and report every mismatch as JSON.",
    )

    return {
        "sow_id": sow_public_id,
        "contract_id": contract["public_id"],
        "deterministic": deterministic,
        "comparison": _parse_json_object(result.get("text")),
        "provider": result.get("provider"),
        "degraded_reason": result.get("reason"),
    }


def _contract_id(conn: AsyncConnection, company_id: uuid.UUID, public_id: str) -> uuid.UUID:
    row = resolve_scoped(conn, "contracts", public_id, company_id, columns="id")
    return uuid.UUID(str(row["id"]))


# =============================================================================
# timesheet intelligence (§27)
# =============================================================================
TIMESHEET_SYSTEM = """You explain timesheet anomalies to a reviewer.
Return ONLY a JSON object:
{"findings": [{"severity": "LOW|MEDIUM|HIGH", "category": string,
               "explanation": string, "evidence": string}]}.
Explain, do not accuse. Every finding must cite the numbers it relies on."""


async def analyse_timesheet(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    user_id: uuid.UUID,
    timesheet_public_id: str,
) -> dict[str, Any]:
    """Deterministic anomaly detection first, then a written explanation."""
    sheet = (
        (
            await conn.execute(
                text(
                    """
                    SELECT t.id, t.public_id, t.total_hours, t.billable_hours, t.total_amount,
                           t.period_start, t.period_end, t.currency,
                           u.public_id AS user_public_id,
                           NULLIF(TRIM(u.first_name || ' ' || u.last_name), '') AS user_name,
                           pr.public_id AS role_public_id, pr.title AS role_title,
                           cr.rate AS contract_rate, cr.max_units, cr.start_date, cr.end_date,
                           c.public_id AS contract_public_id, c.title AS contract_title,
                           c.status AS contract_status
                      FROM public.timesheets t
                      JOIN public.users u ON u.id = t.user_id
                      JOIN public.contracts c ON c.id = t.contract_id
                      LEFT JOIN public.contract_roles cr ON cr.id = t.contract_role_id
                      LEFT JOIN public.project_roles pr ON pr.id = cr.project_role_id
                     WHERE t.public_id = :pid AND t.company_id = :cid
                    """
                ),
                {"pid": timesheet_public_id, "cid": company_id},
            )
        )
        .mappings()
        .first()
    )
    if sheet is None:
        from app.core.errors import ResourceNotFoundError

        raise ResourceNotFoundError("Timesheet not found.")

    entry_rows = (
        (
            await conn.execute(
                text(
                    """
                    SELECT id::text, entry_date, hours, is_billable, work_description,
                           project_task, rate_applied, amount, start_time, end_time
                      FROM public.timesheet_entries
                     WHERE timesheet_id = :tid ORDER BY entry_date
                    """
                ),
                {"tid": sheet["id"]},
            )
        )
        .mappings()
        .all()
    )
    entries: list[dict[str, Any]] = [dict(e) for e in entry_rows]

    findings: list[Finding] = []

    # 1. Unusually high hours for the period length.
    days = ((sheet["period_end"] - sheet["period_start"]).days or 0) + 1
    if as_decimal(sheet["total_hours"]) > Decimal("12") * days:
        findings.append(
            {
                "severity": "HIGH",
                "category": "UNUSUAL_HOURS",
                "explanation": (
                    f"{sheet['total_hours']} hours recorded over {days} days exceeds a 12-hour day "
                    "for the whole period."
                ),
                "evidence": f"total_hours={sheet['total_hours']} days={days}",
            }
        )

    # 2. Weekend work.
    weekend = [e for e in entries if e["entry_date"].weekday() >= 5]
    if weekend:
        findings.append(
            {
                "severity": "MEDIUM",
                "category": "WEEKEND_ACTIVITY",
                "explanation": (
                    f"{len(weekend)} entr{'y' if len(weekend) == 1 else 'ies'} fall on a weekend; "
                    "confirm they were agreed and are billable."
                ),
                "evidence": ", ".join(f"{e['entry_date']} {e['hours']}h" for e in weekend[:8]),
            }
        )

    # 3. Duplicate-looking entries: same date, hours and description.
    seen: dict[tuple[Any, ...], int] = {}
    for entry in entries:
        key = (
            entry["entry_date"],
            str(entry["hours"]),
            (entry["work_description"] or "").strip()[:60],
        )
        seen[key] = seen.get(key, 0) + 1
    duplicates = [k for k, v in seen.items() if v > 1]
    if duplicates:
        findings.append(
            {
                "severity": "HIGH",
                "category": "POSSIBLE_DUPLICATE",
                "explanation": (
                    f"{len(duplicates)} day/hours/description combination(s) appear more "
                    "than once; "
                    "these may be double-counted."
                ),
                "evidence": "; ".join(f"{k[0]} {k[1]}h" for k in duplicates[:5]),
            }
        )

    # 4. Rate disagrees with the contract role.
    rate = sheet["contract_rate"]
    if rate is not None:
        off = [
            e
            for e in entries
            if e["rate_applied"] is not None and as_decimal(e["rate_applied"]) != as_decimal(rate)
        ]
        if off:
            findings.append(
                {
                    "severity": "HIGH",
                    "category": "RATE_MISMATCH",
                    "explanation": (
                        f"{len(off)} entries were priced at something other than the contract rate "
                        f"of {rate}."
                    ),
                    "evidence": "; ".join(
                        f"{e['entry_date']} rate={e['rate_applied']}" for e in off[:5]
                    ),
                }
            )

    # 5. Contract maximum units exceeded.
    if sheet["max_units"] is not None and as_decimal(sheet["billable_hours"]) > as_decimal(
        sheet["max_units"]
    ):
        findings.append(
            {
                "severity": "HIGH",
                "category": "CONTRACT_LIMIT_EXCEEDED",
                "explanation": (
                    f"{sheet['billable_hours']} billable hours exceeds the contractual maximum of "
                    f"{sheet['max_units']}."
                ),
                "evidence": f"billable={sheet['billable_hours']} max={sheet['max_units']}",
            }
        )

    # 6. Entries outside the contract window.
    if sheet["start_date"] and sheet["end_date"]:
        outside = [
            e
            for e in entries
            if e["entry_date"] < sheet["start_date"] or e["entry_date"] > sheet["end_date"]
        ]
        if outside:
            findings.append(
                {
                    "severity": "HIGH",
                    "category": "OUTSIDE_CONTRACT_PERIOD",
                    "explanation": (
                        f"{len(outside)} entries fall outside the contracted dates "
                        f"{sheet['start_date']}..{sheet['end_date']}."
                    ),
                    "evidence": ", ".join(str(e["entry_date"]) for e in outside[:8]),
                }
            )

    # 7. Unusual utilisation against the person's own history.
    baseline = await conn.execute(
        text(
            """
            SELECT COALESCE(avg(billable_hours), 0), count(*)
              FROM public.timesheets
             WHERE user_id = (SELECT user_id FROM public.timesheets WHERE id = :tid)
               AND status IN ('APPROVED','LOCKED')
               AND id <> :tid
            """
        ),
        {"tid": sheet["id"]},
    )
    average, prior_count = baseline.mappings().first() or (0, 0)
    if as_decimal(prior_count) >= 3 and as_decimal(average) > 0:
        ratio = as_decimal(sheet["billable_hours"]) / as_decimal(average)
        if ratio > Decimal("1.75"):
            findings.append(
                {
                    "severity": "MEDIUM",
                    "category": "UNUSUAL_UTILISATION",
                    "explanation": (
                        f"{sheet['billable_hours']} billable hours is {ratio:.1f}x this person's "
                        f"average of {average}."
                    ),
                    "evidence": f"billable={sheet['billable_hours']} average={average}",
                }
            )

    context = (
        f"Timesheet {timesheet_public_id} for {sheet['user_name']} "
        f"({sheet['user_public_id']}) on role {sheet['role_title']} ({sheet['role_public_id']}), "
        f"contract {sheet['contract_public_id']} ({sheet['contract_status']}).\n"
        f"Period {sheet['period_start']}..{sheet['period_end']}, "
        f"total {sheet['total_hours']}h, billable {sheet['billable_hours']}h, "
        f"amount {sheet['total_amount']} {sheet['currency']}.\n"
        f"Entries: {json.dumps(entries, default=str)[:6000]}"
    )
    result = await _chat(
        company_id=company_id,
        user_id=user_id,
        feature="ai:timesheet_intelligence",
        system=TIMESHEET_SYSTEM,
        context=context,
        question="Explain each anomaly and say whether it looks legitimate.",
    )

    return {
        "timesheet_id": timesheet_public_id,
        "user_id": sheet["user_public_id"],
        "role_id": sheet["role_public_id"],
        "contract_id": sheet["contract_public_id"],
        "deterministic_findings": findings,
        "explanation": _parse_json_object(result.get("text")),
        "provider": result.get("provider"),
        "degraded_reason": result.get("reason"),
    }


# =============================================================================
# invoice validation (§28)
# =============================================================================
async def _rate_findings(
    conn: AsyncConnection, *, invoice: dict[str, Any], contract: dict[str, Any]
) -> list[Finding]:
    """Every billed timesheet line must be priced at the contract role's rate."""
    rates = await conn.execute(
        text(
            """
            SELECT pr.title, cr.rate
              FROM public.contract_roles cr
              JOIN public.project_roles pr ON pr.id = cr.project_role_id
             WHERE cr.contract_id = :cid AND cr.rate IS NOT NULL
            """
        ),
        {"cid": contract["id"]},
    )
    contract_rates = {str(r["title"]): as_decimal(r["rate"]) for r in rates.mappings().all()}

    findings: list[Finding] = []
    for item in invoice.get("items", []):
        if item["line_type"] != "TIMESHEET":
            continue
        title = item.get("project_role_title") or ""
        expected = contract_rates.get(title)
        if expected is None:
            continue
        applied = as_decimal(item["unit_rate"])
        if applied != expected:
            findings.append(
                {
                    "severity": "HIGH",
                    "code": "RATE_MISMATCH",
                    "message": (f"'{title}' was billed at {applied}, contract rate {expected}."),
                    "blocking": True,
                    "detail": {"role": title, "billed": str(applied), "contract": str(expected)},
                }
            )
    return findings


async def _history_findings(
    conn: AsyncConnection, *, company_id: uuid.UUID, invoice: dict[str, Any]
) -> list[Finding]:
    """Duplicate detection and amount sanity against this customer's history."""
    # get_invoice exposes the counterparty as a public CO... id; the column is
    # a uuid, so resolve it first (a public id bound as uuid is DataError).
    cp_internal = None
    if invoice.get("counterparty_company_id"):
        cp_row = (
            (
                await conn.execute(
                    text("SELECT id FROM public.companies WHERE public_id = :p"),
                    {"p": invoice["counterparty_company_id"]},
                )
            )
            .mappings()
            .first()
        )
        cp_internal = cp_row["id"] if cp_row else None
    params = {
        "cid": company_id,
        "iid": invoice["id"],
        "cp": cp_internal,
    }
    duplicates = await conn.execute(
        text(
            """
            SELECT public_id, invoice_number
              FROM public.invoices
             WHERE company_id = :cid
               AND id <> CAST(:iid AS uuid)
               AND deleted_at IS NULL
               AND counterparty_company_id IS NOT DISTINCT FROM :cp
               AND total_amount = :total
               AND period_start = :start AND period_end = :end
            """
        ),
        {
            **params,
            "total": as_decimal(invoice["total_amount"]),
            "start": invoice["period_start"],
            "end": invoice["period_end"],
        },
    )
    twins = duplicates.mappings().all()

    history = await conn.execute(
        text(
            """
            SELECT avg(total_amount) AS average, count(*) AS n
              FROM public.invoices
             WHERE company_id = :cid
               AND id <> CAST(:iid AS uuid)
               AND direction = 'RECEIVABLE'
               AND counterparty_company_id IS NOT DISTINCT FROM :cp
               AND deleted_at IS NULL
               AND status NOT IN ('DRAFT','PENDING','CANCELLED','REJECTED')
            """
        ),
        params,
    )
    stats: dict[str, Any] = dict(history.mappings().first() or {})

    findings: list[Finding] = []
    if twins:
        findings.append(
            {
                "severity": "HIGH",
                "code": "POSSIBLE_DUPLICATE_INVOICE",
                "message": (
                    "Another invoice covers the same counterparty, amount and period: "
                    + ", ".join(str(t["invoice_number"] or t["public_id"]) for t in twins)
                ),
                "blocking": True,
                "detail": {"invoices": [str(t["public_id"]) for t in twins]},
            }
        )

    if int(stats.get("n") or 0) >= 3 and as_decimal(stats.get("average")) > 0:
        ratio = as_decimal(invoice["total_amount"]) / as_decimal(stats["average"])
        if ratio > Decimal("3") or ratio < Decimal("0.33"):
            findings.append(
                {
                    "severity": "MEDIUM",
                    "code": "UNUSUAL_AMOUNT",
                    "message": (
                        f"This amount is {ratio:.1f}x the average of {stats['average']} "
                        "for this customer."
                    ),
                    "blocking": False,
                    "detail": {"ratio": round(float(ratio), 2)},
                }
            )
    return findings


async def validate_invoice(
    conn: AsyncConnection, *, company_id: uuid.UUID, invoice: dict[str, Any]
) -> list[Finding]:
    """Pre-approval checks. Deterministic, so they are testable and repeatable."""
    findings: list[Finding] = []
    contract = await resolve_scoped(conn, "contracts", invoice["contract_id"], company_id)

    if as_decimal(invoice["total_amount"]) <= 0:
        findings.append(
            {
                "severity": "HIGH",
                "code": "ZERO_VALUE_INVOICE",
                "message": "This invoice has no value.",
                "blocking": True,
            }
        )

    # Rates must come from the active contract terms (rule 4).
    findings.extend(await _rate_findings(conn, invoice=invoice, contract=contract))

    # Currency must match the contract.
    if invoice["currency"] != contract["currency"]:
        findings.append(
            {
                "severity": "MEDIUM",
                "code": "CURRENCY_MISMATCH",
                "message": (
                    f"This invoice is in {invoice['currency']} while the contract is in "
                    f"{contract['currency']}."
                ),
                "blocking": False,
            }
        )

    # Payment terms must match the contract.
    if int(invoice["payment_terms_days"]) != int(contract["payment_terms_days"]):
        findings.append(
            {
                "severity": "MEDIUM",
                "code": "PAYMENT_TERMS_MISMATCH",
                "message": (
                    f"Payment terms of {invoice['payment_terms_days']} days differ from the "
                    f"contract's {contract['payment_terms_days']} days."
                ),
                "blocking": False,
            }
        )

    findings.extend(await _history_findings(conn, company_id=company_id, invoice=invoice))

    # Billing period sanity.
    if invoice["period_end"] < invoice["period_start"]:
        findings.append(
            {
                "severity": "HIGH",
                "code": "INVALID_PERIOD",
                "message": "The billing period ends before it starts.",
                "blocking": True,
            }
        )

    if invoice.get("msa_required"):
        findings.append(
            {
                "severity": "HIGH",
                "code": "MSA_REQUIRED",
                "message": (
                    invoice.get("msa_block_reason")
                    or "An active Master Service Agreement is required before this can be approved."
                ),
                "blocking": True,
            }
        )

    # Missing information a reviewer must supply.
    if not invoice.get("counterparty_company_id") and not invoice.get("counterparty_user_id"):
        findings.append(
            {
                "severity": "MEDIUM",
                "code": "MISSING_CUSTOMER",
                "message": "No customer is attached to this invoice.",
                "blocking": False,
            }
        )

    return findings


INVOICE_SYSTEM = """You explain invoice review warnings to an approver.
Return ONLY a JSON object: {"summary": string, "questions_to_ask": [string],
"recommended_action": string}. Be brief and specific."""


_SYSTEM_ACTOR = uuid.UUID(int=0)


async def explain_invoice_warnings(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    invoice: dict[str, Any],
    findings: list[Finding],
) -> dict[str, Any]:
    if not findings:
        return {"provider": None, "text": None, "reason": "no_findings"}
    result = await _chat(
        company_id=company_id,
        user_id=invoice.get("created_by_user") or _SYSTEM_ACTOR,
        feature="ai:invoice_validation",
        system=INVOICE_SYSTEM,
        context=(
            f"Invoice {invoice.get('invoice_number') or invoice['public_id']} for "
            f"{invoice.get('counterparty_company_name') or 'n/a'}, total "
            f"{invoice['total_amount']} {invoice['currency']}, period "
            f"{invoice['period_start']}..{invoice['period_end']}.\n"
            f"Items: {json.dumps(invoice.get('items', []), default=str)[:4000]}\n"
            f"Findings: {json.dumps(findings, default=str)}"
        ),
        question="Summarise the risk for the approver and list what they should check.",
    )
    if result.get("provider") is None:
        return {"provider": None, "text": None, "reason": result.get("reason")}
    parsed = _parse_json_object(result.get("text"))
    return {
        "provider": result.get("provider"),
        "summary": parsed.get("summary") or result.get("text"),
        "questions_to_ask": parsed.get("questions_to_ask") or [],
        "recommended_action": parsed.get("recommended_action"),
    }


# =============================================================================
# reconciliation intelligence (§29)
# =============================================================================
async def annotate_match_candidates(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    transaction_id: str,
    candidates: list[dict[str, Any]],
    can: Callable[[str], bool],
) -> None:
    """Add an AI reason to existing rule-based candidates.

    The confidence score is never changed by the model: the rule engine owns the
    number, the model only explains it. Mutates `candidates` in place.
    """
    if not can("ai.financial_intelligence") or not candidates:
        return

    gateway = get_gateway()
    if not gateway.registry.available_chat_keys():
        for candidate in candidates:
            candidate["ai_commentary"] = None
            candidate["ai_unavailable_reason"] = "no_provider_configured"
        return

    txn = (
        (
            await conn.execute(
                text(
                    """
                    SELECT posted_at, amount, currency, description_raw, merchant_name
                      FROM public.bank_transactions
                     WHERE id = CAST(:tid AS uuid) AND company_id = :cid
                    """
                ),
                {"tid": transaction_id, "cid": company_id},
            )
        )
        .mappings()
        .first()
    )
    if txn is None:
        return

    system_actor = uuid.UUID(int=0)
    result = await _chat(
        company_id=company_id,
        user_id=system_actor,
        feature="ai:reconciliation",
        system=(
            "You review a proposed bank-transaction to invoice match. The rule engine has "
            "already scored it. Explain whether the match looks right and name anything that "
            "would make it wrong. Do not change the score. Return JSON: "
            '{"verdict": "LIKELY"|"UNSURE"|"UNLIKELY", "reason": string, "checks": [string]}'
        ),
        context=(
            f"Transaction: {txn['amount']} {txn['currency']} on {txn['posted_at']}, "
            f"description '{txn['description_raw']}', merchant '{txn['merchant_name']}'.\n"
            f"Candidates: {json.dumps(candidates, default=str)[:5000]}"
        ),
        question="Review the candidate list and comment on the top candidate.",
    )

    parsed = _parse_json_object(result.get("text"))
    for index, candidate in enumerate(candidates):
        if index == 0 and parsed:
            candidate["ai_commentary"] = {
                "verdict": parsed.get("verdict"),
                "reason": parsed.get("reason"),
                "checks": parsed.get("checks") or [],
            }
        else:
            candidate["ai_commentary"] = None
    if result.get("provider") is None:
        for candidate in candidates:
            candidate["ai_unavailable_reason"] = result.get("reason")


# =============================================================================
# project intelligence (§30)
# =============================================================================
async def project_health(
    conn: AsyncConnection, *, company_id: uuid.UUID, project_public_id: str
) -> dict[str, Any]:
    """Health signals for one project, each with the numbers behind it."""
    project = (
        (
            await conn.execute(
                text(
                    """
                    SELECT id, public_id, name, status, currency, estimated_budget,
                           start_date, estimated_end_date, health_score
                      FROM public.projects
                     WHERE public_id = :pid AND company_id = :cid AND deleted_at IS NULL
                    """
                ),
                {"pid": project_public_id, "cid": company_id},
            )
        )
        .mappings()
        .first()
    )
    if project is None:
        from app.core.errors import ResourceNotFoundError

        raise ResourceNotFoundError("Project not found.")

    signals: dict[str, Any] = {}

    budget = await conn.execute(
        text(
            """
            SELECT COALESCE(sum(i.total_amount) FILTER (
                     WHERE i.status NOT IN ('DRAFT','PENDING','CANCELLED','REJECTED')
                   ), 0) AS invoiced,
                   COALESCE(sum(i.balance_due) FILTER (
                     WHERE i.status NOT IN ('CANCELLED','REJECTED','REFUNDED')
                   ), 0) AS outstanding,
                   COALESCE(sum(c.contract_value), 0) AS contracted
              FROM public.invoices i
              LEFT JOIN public.contracts c ON c.id = i.contract_id
             WHERE i.project_id = :pid AND i.direction = 'RECEIVABLE' AND i.deleted_at IS NULL
            """
        ),
        {"pid": project["id"]},
    )
    money_row: dict[str, Any] = dict(budget.mappings().first() or {})
    invoiced = money(money_row.get("invoiced"))
    outstanding = money(money_row.get("outstanding"))
    budget_limit = project["estimated_budget"]

    signals["budget"] = {
        "budget": money(budget_limit) if budget_limit is not None else None,
        "invoiced": invoiced,
        "outstanding": outstanding,
        "contracted": money(money_row.get("contracted")),
        "utilisation_pct": (
            round(float(invoiced / as_decimal(budget_limit)) * 100, 2)
            if budget_limit is not None and as_decimal(budget_limit) > 0
            else None
        ),
        "over_budget": bool(budget_limit is not None and invoiced > as_decimal(budget_limit)),
    }

    signals["schedule"] = await _schedule_signals(conn, project)
    signals["staffing"] = await _staffing_signals(conn, project, company_id)
    signals["billing"] = await _project_billing_signals(conn, project)
    signals["contracts"] = await _project_contract_signals(conn, project)
    signals["overdue"] = await _project_overdue_signals(conn, project)

    score, drivers = _health_score(signals)
    await conn.execute(
        text(
            """
            UPDATE public.projects SET health_score = :score, health_computed_at = now()
             WHERE id = :pid
            """
        ),
        {"score": score, "pid": project["id"]},
    )

    return {
        "project_id": project_public_id,
        "project_name": project["name"],
        "status": project["status"],
        "health_score": score,
        "health_drivers": drivers,
        "signals": signals,
    }


async def _schedule_signals(conn: AsyncConnection, project: Any) -> dict[str, Any]:
    today = utc_today()
    overdue_tasks = await conn.execute(
        text(
            """
            SELECT count(*) FROM public.invoices i
             WHERE i.project_id = :pid AND i.balance_due > 0 AND i.due_date < current_date
               AND i.status NOT IN ('CANCELLED','REJECTED','REFUNDED')
            """
        ),
        {"pid": project["id"]},
    )
    end = project["estimated_end_date"]
    days_left = (end - today).days if end else None
    return {
        "start_date": project["start_date"],
        "estimated_end_date": end,
        "days_remaining": days_left,
        "overdue": bool(
            days_left is not None and days_left < 0 and project["status"] != "COMPLETED"
        ),
        "overdue_invoices": int(overdue_tasks.scalar() or 0),
    }


async def _staffing_signals(
    conn: AsyncConnection, project: Any, company_id: uuid.UUID
) -> dict[str, Any]:
    gaps = await conn.execute(
        text(
            """
            SELECT count(*) FROM public.project_roles r
             WHERE r.project_id = :pid AND r.deleted_at IS NULL
               AND r.status <> 'CLOSED' AND r.allocated_count < r.required_count
            """
        ),
        {"pid": project["id"]},
    )
    unfilled = await conn.execute(
        text(
            """
            SELECT COALESCE(sum(r.required_count - r.allocated_count), 0)
              FROM public.project_roles r
             WHERE r.project_id = :pid AND r.deleted_at IS NULL AND r.status <> 'CLOSED'
            """
        ),
        {"pid": project["id"]},
    )
    stale = await conn.execute(
        text(
            """
            SELECT count(*) FROM public.project_roles r
             WHERE r.project_id = :pid AND r.deleted_at IS NULL
               AND r.required_count > r.allocated_count
               AND r.updated_at < now() - interval '30 days'
            """
        ),
        {"pid": project["id"]},
    )
    return {
        "unfilled_roles": int(gaps.scalar() or 0),
        "headcount_gap": int(unfilled.scalar() or 0),
        "roles_stale_over_30_days": int(stale.scalar() or 0),
    }


async def _project_billing_signals(conn: AsyncConnection, project: Any) -> dict[str, Any]:
    pending = await conn.execute(
        text(
            """
            SELECT COALESCE(sum(cr.rate * COALESCE(t.billed_hours,0)), 0) AS potential
              FROM public.contract_roles cr
              LEFT JOIN public.project_roles pr ON pr.id = cr.project_role_id
              LEFT JOIN LATERAL (
                    SELECT sum(billable_hours) AS billed_hours FROM public.timesheets t
                     WHERE t.contract_role_id = cr.id AND t.status = 'APPROVED'
              ) t ON TRUE
             WHERE cr.project_role_id IN (
                   SELECT id FROM public.project_roles WHERE project_id = :pid)
            """
        ),
        {"pid": project["id"]},
    )
    unbilled = await conn.execute(
        text(
            """
            SELECT count(*) FROM public.timesheets t
             WHERE t.project_id = :pid AND t.status = 'APPROVED'
               AND NOT EXISTS (SELECT 1 FROM public.invoice_items i
                                WHERE i.source_timesheet_id = t.id)
            """
        ),
        {"pid": project["id"]},
    )
    return {
        "approved_value": money((pending.mappings().first() or {}).get("potential")),
        "unbilled_approved_timesheets": int(unbilled.scalar() or 0),
    }


async def _project_contract_signals(conn: AsyncConnection, project: Any) -> dict[str, Any]:
    rows = await conn.execute(
        text(
            """
            SELECT status, count(*) FROM public.contracts
             WHERE project_id = :pid AND deleted_at IS NULL GROUP BY status
            """
        ),
        {"pid": project["id"]},
    )
    counts = {str(r["status"]): int(r["count"]) for r in rows.mappings().all()}
    expiring = await conn.execute(
        text(
            """
            SELECT count(*) FROM public.contracts
             WHERE project_id = :pid AND deleted_at IS NULL
               AND status IN ('ACCEPTED','ACTIVE')
               AND end_date IS NOT NULL AND end_date <= current_date + 60
            """
        ),
        {"pid": project["id"]},
    )
    return {
        "by_status": counts,
        "expiring_within_60_days": int(expiring.scalar() or 0),
        "without_active_contract": counts.get("ACTIVE", 0) == 0 and counts.get("ACCEPTED", 0) == 0,
    }


async def _project_overdue_signals(conn: AsyncConnection, project: Any) -> dict[str, Any]:
    rows = await conn.execute(
        text(
            """
            SELECT i.public_id, i.invoice_number, i.due_date, i.balance_due, i.currency
              FROM public.invoices i
             WHERE i.project_id = :pid AND i.balance_due > 0
               AND i.status NOT IN ('CANCELLED','REJECTED','REFUNDED')
               AND i.due_date < current_date AND i.deleted_at IS NULL
             ORDER BY i.due_date LIMIT 20
            """
        ),
        {"pid": project["id"]},
    )
    return {"invoices": [dict(r) for r in rows.mappings().all()]}


def _health_score(signals: dict[str, Any]) -> tuple[int, list[str]]:
    """A 0-100 score with the reasons that moved it. Transparent, not a black box."""
    score = 100
    drivers: list[str] = []

    budget = signals["budget"]
    if budget["over_budget"]:
        score -= 30
        drivers.append("Invoiced value exceeds the approved budget.")
    elif budget["utilisation_pct"] and budget["utilisation_pct"] > 90:
        score -= 10
        drivers.append("Budget utilisation is above 90%.")

    schedule = signals["schedule"]
    if schedule["overdue"]:
        score -= 20
        drivers.append("The project is past its estimated end date and still open.")
    if schedule["overdue_invoices"]:
        score -= min(15, schedule["overdue_invoices"] * 3)
        drivers.append(f"{schedule['overdue_invoices']} overdue invoice(s).")

    staffing = signals["staffing"]
    if staffing["unfilled_roles"]:
        score -= min(20, staffing["unfilled_roles"] * 5)
        drivers.append(f"{staffing['unfilled_roles']} role(s) are not fully staffed.")
    if staffing["roles_stale_over_30_days"]:
        score -= 5
        drivers.append("Some unfilled roles have not been reviewed in 30 days.")

    billing = signals["billing"]
    if billing["unbilled_approved_timesheets"]:
        score -= min(15, billing["unbilled_approved_timesheets"] * 3)
        drivers.append("Approved time is waiting to be invoiced.")

    contracts = signals["contracts"]
    if contracts["without_active_contract"] and schedule["days_remaining"] not in (None,):
        score -= 15
        drivers.append("No active contract covers this project.")
    if contracts["expiring_within_60_days"]:
        score -= min(15, contracts["expiring_within_60_days"] * 5)
        drivers.append("A contract expires within 60 days.")

    return max(0, min(100, score)), drivers


# =============================================================================
# financial intelligence (§31)
# =============================================================================
async def financial_overview(
    conn: AsyncConnection, *, company_id: uuid.UUID, user_id: uuid.UUID
) -> dict[str, Any]:
    """Receivables, forecast and anomaly summary for the finance dashboard."""
    from app.services.invoicing import receivables_summary

    summary = await receivables_summary(conn, company_id=company_id)

    forecast = await conn.execute(
        text(
            """
            SELECT to_char(date_trunc('month', d.month)::date, 'YYYY-MM') AS month,
                   COALESCE(sum(i.balance_due) FILTER (
                     WHERE i.due_date <= (d.month + interval '1 month - 1 day')::date
                       AND i.status NOT IN ('CANCELLED','REJECTED','REFUNDED')
                   ), 0) AS expected
              FROM (
                    SELECT generate_series(
                             date_trunc('month', current_date),
                             date_trunc('month', current_date) + interval '5 months',
                             interval '1 month') AS month
              ) d
              LEFT JOIN public.invoices i ON i.company_id = :cid AND i.deleted_at IS NULL
             GROUP BY 1 ORDER BY 1
            """
        ),
        {"cid": company_id},
    )

    anomalies = await conn.execute(
        text(
            """
            SELECT i.public_id, i.invoice_number, i.total_amount, i.currency, i.status,
                   i.due_date, COALESCE(c.display_name, c.legal_name, 'n/a') AS counterparty
              FROM public.invoices i
              LEFT JOIN public.companies c ON c.id = i.counterparty_company_id
             WHERE i.company_id = :cid AND i.deleted_at IS NULL
               AND (
                     (i.balance_due > 0 AND i.due_date < current_date - interval '30 days')
                     OR i.status = 'DISPUTED'
                     OR (i.total_amount < 0)
                   )
             ORDER BY i.due_date LIMIT 25
            """
        ),
        {"cid": company_id},
    )

    result = await _chat(
        company_id=company_id,
        user_id=user_id,
        feature="ai:financial_intelligence",
        system=(
            "You are advising a finance manager. Return ONLY a JSON object: "
            '{"headline": string, "risks": [string], "actions": [string]}. '
            "Base every statement on the figures supplied."
        ),
        context=json.dumps(
            {
                "outstanding": summary["outstanding"],
                "overdue": summary["overdue"],
                "aging": summary["aging"],
                "late_payers": summary["late_payers"],
            },
            default=str,
        ),
        question="Summarise the position and the three most useful actions.",
    )

    return {
        "receivables": summary,
        "cash_forecast": [
            {"month": r["month"], "expected": money(r["expected"])}
            for r in forecast.mappings().all()
        ],
        "billing_anomalies": [dict(r) for r in anomalies.mappings().all()],
        "commentary": _parse_json_object(result.get("text")) or None,
        "provider": result.get("provider"),
        "degraded_reason": result.get("reason"),
    }


# =============================================================================
# global assistant tools (§32)
# =============================================================================
ASSISTANT_SYSTEM = """You answer questions about the user's own business data.
Rules:
1. Use only the context supplied. If it does not contain the answer, say so.
2. Never invent an id, amount or date.
3. Answer in at most five short sentences, then list the ids involved.
4. If the question asks for a change, describe the action and stop. Changes need
   human approval."""


async def assistant(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    user_id: uuid.UUID,
    question: str,
    actor_permissions: frozenset[str],
) -> dict[str, Any]:
    """Answer a natural-language question over data the caller may read.

    The context is assembled with the caller's own permissions applied, so a
    question about invoices only sees invoices when the caller holds
    `invoices.read`, and the same rule holds for every other domain.
    """
    from app.services.dashboard import company_snapshot

    snapshot = await company_snapshot(
        conn, company_id=company_id, permissions=actor_permissions, user_id=user_id
    )

    result = await _chat(
        company_id=company_id,
        user_id=user_id,
        feature="ai:assistant",
        system=ASSISTANT_SYSTEM,
        context=json.dumps(snapshot, default=str)[:24000],
        question=question,
    )
    return {
        "question": question,
        "answer": result.get("text"),
        "provider": result.get("provider"),
        "citations": result.get("citations", []),
        "degraded_reason": result.get("reason"),
        "context_summary": {
            "domains_included": sorted(snapshot.get("domains", [])),
            "counts": snapshot.get("counts", {}),
        },
    }


# =============================================================================
# workforce intelligence
# =============================================================================
async def workforce_overview(conn: AsyncConnection, *, company_id: uuid.UUID) -> dict[str, Any]:
    """Utilisation and capacity across the workforce."""
    utilisation = await conn.execute(
        text(
            """
            SELECT u.public_id AS user_public_id,
                   NULLIF(TRIM(u.first_name || ' ' || u.last_name), '') AS user_name,
                   count(DISTINCT a.id) AS assignments,
                   COALESCE(sum(a.allocation_pct), 0) AS allocation_pct,
                   COALESCE(sum(t.billable_hours), 0) AS billable_hours,
                   COALESCE(sum(t.total_hours), 0) AS total_hours
              FROM public.company_memberships m
              JOIN public.users u ON u.id = m.user_id
              LEFT JOIN public.assignments a
                     ON a.user_id = m.user_id AND a.status IN ('ACTIVE','ON_LEAVE')
              LEFT JOIN public.timesheets t
                     ON t.assignment_id = a.id AND t.status IN ('APPROVED','LOCKED')
             WHERE m.company_id = :cid AND m.status = 'ACTIVE'
             GROUP BY u.public_id, u.first_name, u.last_name
             ORDER BY allocation_pct DESC
             LIMIT 100
            """
        ),
        {"cid": company_id},
    )
    rows = []
    for row in utilisation.mappings().all():
        allocation = as_decimal(row["allocation_pct"])
        entries = {
            "user_id": row["user_public_id"],
            "user_name": row["user_name"],
            "assignments": int(row["assignments"]),
            "allocation_pct": float(allocation),
            "billable_hours": as_decimal(row["billable_hours"]),
            "total_hours": as_decimal(row["total_hours"]),
        }
        if allocation >= Decimal("100"):
            entries["flag"] = "OVER_ALLOCATED"
        elif allocation == 0 and as_decimal(row["assignments"]) > 0:
            entries["flag"] = "NO_BILLED_WORK"
        rows.append(entries)

    pending_leave = await conn.execute(
        text(
            """
            SELECT count(*) FROM public.leave_requests
             WHERE company_id = :cid AND status = 'PENDING'
            """
        ),
        {"cid": company_id},
    )
    return {
        "people": rows,
        "pending_leave_requests": int(pending_leave.scalar() or 0),
        "over_allocated": sum(1 for r in rows if r.get("flag") == "OVER_ALLOCATED"),
    }


# =============================================================================
# agent surface
# =============================================================================
def available_agents() -> list[dict[str, Any]]:
    """The registered agents with the tools each one offers.

    The API layer filters the tools by the caller's permissions; this returns the
    full catalogue so the UI can explain what is available.
    """
    agents: Any = get_agents().list()

    _raise: Any = object()

    def _get(item: Any, name: str, default: Any = _raise) -> Any:
        # The registry yields plain dicts; attribute access keeps working if it
        # ever yields objects instead.
        if isinstance(item, dict):
            if name in item:
                return item[name]
        elif hasattr(item, name):
            return getattr(item, name)
        if default is _raise:
            raise KeyError(name)
        return default

    catalogue: list[dict[str, Any]] = [
        {
            "key": _get(agent, "key"),
            "display_name": _get(agent, "display_name"),
            "purpose": _get(agent, "purpose"),
            "tools": [
                {
                    "name": _get(tool, "name"),
                    "description": _get(tool, "description"),
                    "required_permission": _get(tool, "required_permission"),
                    "risk_level": _get(tool, "risk_level"),
                    "mutating": _get(tool, "mutating"),
                }
                # A catalogue entry without tools is valid (no capabilities
                # to offer); it must not fail the whole listing.
                for tool in _get(agent, "tools", [])
            ],
        }
        for agent in agents
    ]
    return catalogue
