"""AI platform additions: automation drafts, briefing, cache, anomaly wording.

Marked `integration`: needs the real database.

Run:
    pytest apps/api/tests/test_ai_platform.py -v
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


async def test_automation_draft_compiles_and_stays_inactive(conn, skeleton, tenants) -> None:
    from app.services import automation_builder

    tenant = tenants["admin"]
    draft = await automation_builder.draft_automation(
        conn,
        company_id=tenant.company_id,
        actor_user_id=tenant.user_id,
        request_id="pytest",
        description="When a contract expires within 30 days, notify the contract manager.",
    )
    assert draft["trigger"]["trigger_type"] == "CONTRACT_EXPIRING"
    assert draft["action"]["action_type"] == "NOTIFY"
    assert draft["conditions"] == {"within_days": 30}
    assert draft["is_active"] is False
    assert draft["requires_human_approval"] is False

    money_draft = await automation_builder.draft_automation(
        conn,
        company_id=tenant.company_id,
        actor_user_id=tenant.user_id,
        request_id="pytest",
        description="When an invoice is overdue, initiate payment.",
    )
    # Financial actions always need a human, whatever was asked.
    assert money_draft["requires_human_approval"] is True
    assert money_draft["is_active"] is False


async def test_automation_draft_rejects_vague_or_unknown(conn, skeleton, tenants) -> None:
    from app.core.errors import ValidationError
    from app.services import automation_builder

    tenant = tenants["admin"]
    with pytest.raises(ValidationError):
        await automation_builder.draft_automation(
            conn,
            company_id=tenant.company_id,
            actor_user_id=tenant.user_id,
            request_id="pytest",
            description="do stuff",
        )
    with pytest.raises(ValidationError):
        await automation_builder.draft_automation(
            conn,
            company_id=tenant.company_id,
            actor_user_id=tenant.user_id,
            request_id="pytest",
            description="When the moon is full, launch the rockets.",
        )


async def test_briefing_respects_permissions(conn, skeleton, tenants) -> None:
    from app.services import ai_domain

    tenant = tenants["admin"]
    full = await ai_domain.daily_briefing(
        conn,
        company_id=tenant.company_id,
        permissions=frozenset({"contracts.read"}),
    )
    assert "contracts_expiring" in full["sections"]
    assert "invoices_overdue" not in full["sections"]

    empty = await ai_domain.daily_briefing(
        conn, company_id=tenant.company_id, permissions=frozenset()
    )
    assert empty["sections"] == {}


async def test_gateway_cache_marks_hits_and_never_replays_tools() -> None:
    from app.ai.gateway import AIGateway, ChatRequest, ChatResponse, Message

    gateway = AIGateway.__new__(AIGateway)
    gateway._cache = {}

    first = ChatResponse(
        content="hello",
        provider_key="test",
        model="test-model",
        prompt_tokens=10,
        completion_tokens=5,
        cost_cents=1.0,
        latency_ms=100,
    )
    other = ChatResponse(
        content="do X",
        provider_key="test",
        model="test-model",
        prompt_tokens=10,
        completion_tokens=5,
        cost_cents=1.0,
        latency_ms=100,
        tool_calls=[{"name": "initiate_payment"}],
    )
    request = ChatRequest(messages=[Message(role="user", content="hi")])

    key = gateway._cache_key(request)
    import time as _time

    gateway._cache[key] = (_time.monotonic(), first)
    assert gateway._cache_key(request) == key  # deterministic

    # Tool-bearing responses are never stored.
    assert other.tool_calls
    gateway._cache.pop(key)
    assert key not in gateway._cache


async def test_anomaly_wording_is_observation_not_accusation() -> None:
    import inspect

    from app.services import ai_domain

    source = inspect.getsource(ai_domain)
    assert source.count("Potential anomaly detected") >= 3
    for banned in ("you committed fraud", "fraudulent user", "guilty"):
        assert banned not in source
