"""AI endpoints.

Three surfaces:

  /ai/capabilities   what is enabled right now, for this user and company
  /ai/assistant      company RAG question answering
  /ai/agents         agent proposal + human approval workflow

All of them resolve feature flags and user consent first, and all of them run
inside the caller's company context so RLS and the retrieval predicate both
apply. No AI route reads data the caller could not read through the normal API.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.ai import agents as agent_service
from app.ai import rag
from app.ai.gateway import ChatRequest, get_gateway
from app.api.deps import (
    RequestContext,
    company_scope,
    require_company_member,
    require_permission,
)
from app.core.errors import PermissionDeniedError
from app.core.logging import get_logger
from app.core.rate_limit import rate_limited
from app.schemas.common import AckResponse
from app.services import flags

router = APIRouter(prefix="/ai", tags=["ai"])
logger = get_logger(__name__)

Context = Annotated[tuple[RequestContext, AsyncConnection], Depends(require_company_member)]
AssistantContext = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("ai.assistant"))
]
AgentContext = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("ai.actions.approve"))
]


# ------------------------------------------------------------------ capability
@router.get("/capabilities", summary="What AI is enabled for this account")
async def capabilities(ctx_and_conn: Context) -> dict[str, Any]:
    """The UI hides what is off; the gateway refuses it again server-side.

    A feature being switched off here is a statement about the environment, not
    an authorization decision, so any member may read it.
    """
    ctx, conn = ctx_and_conn

    resolved = await flags.resolve_many(
        conn,
        [
            "ai.assistant",
            "ai.contract_intelligence",
            "ai.document_intelligence",
            "ai.project_intelligence",
            "ai.workforce_intelligence",
            "ai.financial_intelligence",
            "ai.automations",
            "ai.agents",
            "ai.timesheet_import",
            "ai.w9_intelligence",
        ],
        company_id=ctx.company_id,
        user_id=ctx.user_id,
    )

    # User consent is a second switch: the platform flag alone is not enough.
    consent = (
        await conn.execute(
            text("SELECT ai_settings FROM public.users WHERE id = :uid"), {"uid": ctx.user_id}
        )
    ).scalar() or {}

    assistant_allowed = bool(consent.get("assistant_enabled"))

    from app.ai.gateway import get_gateway as _gateway

    return {
        "gateway": _gateway().registry.capabilities(),
        "features": {
            key: {
                "enabled": flag.enabled and (assistant_allowed or key != "ai.assistant"),
                "config": flag.config,
            }
            for key, flag in resolved.items()
        },
        "consent": {
            "assistant_enabled": assistant_allowed,
            "document_ai_enabled": bool(consent.get("document_ai_enabled")),
            "automations_enabled": bool(consent.get("automations_enabled")),
        },
    }


# ------------------------------------------------------------------- assistant
@router.post(
    "/assistant",
    summary="Ask a question about authorised company data",
    dependencies=[Depends(rate_limited("ai"))],
)
async def ask(ctx_and_conn: AssistantContext, payload: dict[str, Any]) -> dict[str, Any]:
    """RAG over the caller's company, permission-filtered before retrieval.

    Returns `answered: false` with a reason when nothing relevant is visible, so
    the client can distinguish "no data" from "here is the answer".
    """
    ctx, conn = ctx_and_conn

    question = str(payload.get("question") or "").strip()
    if not question:
        from app.core.errors import ValidationError

        raise ValidationError("A question is required.")

    await flags.require(conn, "ai.assistant", company_id=ctx.company_id, user_id=ctx.user_id)

    consent = (
        await conn.execute(
            text("SELECT ai_settings FROM public.users WHERE id = :uid"), {"uid": ctx.user_id}
        )
    ).scalar() or {}

    if not consent.get("assistant_enabled"):
        raise PermissionDeniedError(
            "You have not enabled the AI assistant for your account.",
            request_id=ctx.request_id,
        )

    conversation_public_id = str(payload.get("conversation_id") or "")

    # Step 1: permission-filtered retrieval. Unauthorized content never reaches
    # the model, so there is nothing for it to leak.
    chunks = await rag.retrieve(
        conn,
        company_id=ctx.company_id,  # type: ignore[arg-type]
        user_id=ctx.user_id,
        query=question,
    )
    context_text, citations = rag.build_context(chunks)

    if not chunks:
        return {
            "answered": False,
            "reason": "no_authorised_sources",
            "message": (
                "I could not find information you are authorised to read that answers this."
            ),
            "citations": [],
        }

    # Step 2: generation with the retrieved context fenced as untrusted data.
    messages = rag.build_messages(question, context_text, citations)
    response = await get_gateway().chat(
        ChatRequest(
            messages=messages,
            feature="assistant",
            company_id=ctx.company_id,
            user_id=ctx.user_id,
            context=citations,
        )
    )

    return {
        "answered": True,
        "answer": response.content,
        "citations": [
            {
                "document_public_id": c.document_public_id,
                "title": c.title,
                "page_number": c.page_number,
                "section_path": c.section_path,
                "similarity": round(c.similarity, 4),
            }
            for c in citations
        ],
        "sources_summary": rag.summarise_sources(citations),
        "provider": response.provider_key,
        "model": response.model,
        "tokens_used": response.prompt_tokens + response.completion_tokens,
        "cost_cents": response.cost_cents,
        "conversation_id": conversation_public_id or None,
    }


# ---------------------------------------------------------------------- agents
@router.get("/agents", summary="List available agents")
async def list_agents(ctx_and_conn: Context) -> dict[str, Any]:
    ctx, _conn = ctx_and_conn
    return {"agents": agent_service.get_agents().list()}


@router.post(
    "/agents/{agent_key}/plan",
    response_model=dict[str, Any],
    summary="Ask an agent to propose an action",
    dependencies=[Depends(rate_limited("ai"))],
)
async def plan_action(
    ctx_and_conn: AgentContext, agent_key: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Proposes only. Nothing is executed by this endpoint.

    Tools the caller lacks permission for are not even offered to the model, so
    it cannot even name a capability the user lacks.
    """
    ctx, conn = ctx_and_conn

    await flags.require(conn, "ai.agents", company_id=ctx.company_id, user_id=ctx.user_id)

    run = await agent_service.plan(
        conn,
        company_id=ctx.company_id,  # type: ignore[arg-type]
        user_id=ctx.user_id,
        agent_key=agent_key,
        objective=str(payload.get("objective") or ""),
        context_text=str(payload.get("context") or ""),
        request_id=ctx.request_id,
    )

    return {
        "agent": run.agent_key,
        "status": run.status,
        "action_public_id": run.action_public_id or None,
        "requires_approval": run.requires_approval,
        "proposal": run.proposal,
        "rationale": run.rationale,
        "citations": [
            {
                "document_public_id": c.document_public_id,
                "title": c.title,
                "page_number": c.page_number,
            }
            for c in run.citations
        ],
    }


@router.post(
    "/actions/{action_public_id}/approve",
    response_model=AckResponse,
    summary="Approve a proposed action",
)
async def approve_action(
    ctx_and_conn: AgentContext, action_public_id: str, payload: dict[str, str]
) -> AckResponse:
    """Human decision. The approver must hold the action's required permission."""
    ctx, conn = ctx_and_conn

    result = await agent_service.approve(
        conn,
        company_id=ctx.company_id,  # type: ignore[arg-type]
        action_public_id=action_public_id,
        approver_id=ctx.user_id,
        permission=payload.get("required_permission", "ai.actions.approve"),
        request_id=ctx.request_id,
        notes=payload.get("notes"),
    )
    return AckResponse(
        ok=True, message=f"Action {result['status'].lower()}.", request_id=ctx.request_id
    )


@router.post(
    "/actions/{action_public_id}/reject",
    response_model=AckResponse,
    summary="Reject a proposed action",
)
async def reject_action(
    ctx_and_conn: AgentContext, action_public_id: str, payload: dict[str, str]
) -> AckResponse:
    ctx, conn = ctx_and_conn
    await agent_service.reject(
        conn,
        company_id=ctx.company_id,  # type: ignore[arg-type]
        action_public_id=action_public_id,
        approver_id=ctx.user_id,
        request_id=ctx.request_id,
        reason=payload.get("reason", "No reason provided."),
    )
    return AckResponse(ok=True, message="Action rejected.", request_id=ctx.request_id)


@router.post(
    "/actions/{action_public_id}/execute",
    response_model=dict[str, Any],
    status_code=status.HTTP_200_OK,
    summary="Execute an approved action",
)
async def execute_action(ctx_and_conn: AgentContext, action_public_id: str) -> dict[str, Any]:
    """Executes only what a human approved.

    Preconditions (approval recorded, permission held, capability token present,
    different approver for CRITICAL actions) are enforced by a database trigger,
    so this endpoint cannot be used to bypass them.
    """
    ctx, conn = ctx_and_conn
    return await agent_service.execute(
        conn,
        company_id=ctx.company_id,  # type: ignore[arg-type]
        action_public_id=action_public_id,
        executor_id=ctx.user_id,
        request_id=ctx.request_id,
    )


@router.get("/usage", summary="AI usage and cost for the caller")
async def usage(
    ctx_and_conn: Annotated[
        tuple[RequestContext, AsyncConnection], Depends(require_permission("ai.usage.read"))
    ],
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn

    row = (
        (
            await conn.execute(
                text(
                    """
                SELECT count(*)::int AS requests,
                       COALESCE(sum(prompt_tokens + completion_tokens), 0)::bigint AS tokens,
                       COALESCE(sum(estimated_cost_cents), 0) AS cost_cents
                  FROM public.ai_usage_events
                 WHERE user_id = :uid
                   AND created_at >= date_trunc('day', now())
                """
                ),
                {"uid": ctx.user_id},
            )
        )
        .mappings()
        .first()
    )

    usage: dict[str, Any] = dict(row or {})
    return {"period": "today", **usage}


@router.get(
    "/briefing",
    summary="Personalized daily briefing",
    dependencies=[Depends(rate_limited("ai"))],
)
async def briefing(ctx_and_conn: Context) -> dict[str, Any]:
    """What needs attention: expiring contracts, overdue invoices, pending
    timesheets and MSA requests — each section gated on its own permission."""
    from app.services import ai_domain as briefing_service

    ctx, conn = ctx_and_conn
    result = await briefing_service.daily_briefing(
        conn, company_id=company_scope(ctx), permissions=ctx.permissions
    )
    return {**result, "request_id": ctx.request_id}


@router.post(
    "/automations/draft",
    summary="Draft an automation from words",
    dependencies=[Depends(rate_limited("ai"))],
)
async def draft_automation(
    ctx_and_conn: Annotated[
        tuple[RequestContext, AsyncConnection],
        Depends(require_permission("ai.automations.manage")),
    ],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Compile a description into an inactive draft. Deterministic, never
    generative: unrecognized parts come back as `unparsed` for the human."""
    from app.services import automation_builder

    ctx, conn = ctx_and_conn
    return await automation_builder.draft_automation(
        conn,
        company_id=company_scope(ctx),
        actor_user_id=ctx.user_id,
        request_id=ctx.request_id,
        description=str(payload.get("description") or ""),
        name=payload.get("name"),
    )
