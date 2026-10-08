"""Notifications, the company dashboard, and the AI domain intelligence surface."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.api.deps import RequestContext, company_scope, require_permission
from app.core.logging import get_logger
from app.schemas.common import Page, build_page, clamp_limit, decode_cursor
from app.services import ai_domain
from app.services import dashboard as dashboard_service

router = APIRouter(tags=["platform"])
logger = get_logger(__name__)

NotificationsRead = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("notifications.read"))
]
DashboardRead = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("dashboard.read"))
]
AiRead = Annotated[tuple[RequestContext, AsyncConnection], Depends(require_permission("ai.read"))]
AiContract = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("ai.contract_intelligence"))
]
AiProject = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("ai.project_intelligence"))
]
AiFinancial = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("ai.financial_intelligence"))
]
AiWorkforce = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("ai.workforce_intelligence"))
]
AiAssistant = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("ai.assistant"))
]
MemberContext = Annotated[
    tuple[RequestContext, AsyncConnection], Depends(require_permission("dashboard.read"))
]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AssistantBody(_Body):
    question: str = Field(..., min_length=3, max_length=2000)


class ContractFactsBody(_Body):
    contract_id: str | None = None
    document_id: str | None = None
    text: str | None = Field(default=None, max_length=400000)


class SowCompareBody(_Body):
    sow_id: str = Field(..., min_length=4, max_length=32)


# =============================================================================
# notifications
# =============================================================================
@router.get("/notifications", response_model=Page[dict[str, Any]], summary="My notifications")
async def list_notifications(
    ctx_and_conn: NotificationsRead,
    unread_only: bool = Query(False),
    category: str | None = Query(None, max_length=32),
    limit: int = Query(25, ge=1, le=100),
    cursor: str | None = Query(None),
) -> Page[Any]:
    ctx, conn = ctx_and_conn
    page_size = clamp_limit(limit, default=25, maximum=100)
    keys = decode_cursor(cursor) if cursor else {}
    where = ["n.user_id = :uid"]
    params: dict[str, Any] = {"uid": ctx.user_id, "limit": page_size + 1}
    if unread_only:
        where.append("n.read_at IS NULL")
    if category:
        where.append("n.category = :category")
        params["category"] = category
    if keys.get("created_at"):
        where.append("(n.created_at, n.id) < (:cur_created, :cur_id)")
        params["cur_created"] = keys["created_at"]
        params["cur_id"] = keys.get("id", "")

    rows = (
        (
            await conn.execute(
                text(
                    f"""
                    SELECT n.id::text, n.category, n.title, n.body, n.action_url,
                           n.entity_type, n.read_at, n.created_at, n.metadata
                      FROM platform.notifications n
                     WHERE {" AND ".join(where)}
                     ORDER BY n.created_at DESC, n.id DESC
                     LIMIT :limit
                    """  # noqa: S608
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    return build_page(
        [dict(r) for r in rows],
        limit=page_size,
        cursor_keys=("created_at", "id"),
        request_id=ctx.request_id,
    )


@router.get("/notifications/unread-count", summary="Unread counts")
async def unread_counts(ctx_and_conn: NotificationsRead) -> dict[str, int]:
    """Counts that drive the navigation badges."""
    ctx, conn = ctx_and_conn
    notifications = await conn.execute(
        text(
            "SELECT count(*) FROM platform.notifications WHERE user_id = :uid AND read_at IS NULL"
        ),
        {"uid": ctx.user_id},
    )
    approvals = await conn.execute(
        text(
            """
            SELECT
              (SELECT count(*) FROM public.timesheet_approvals ta
                JOIN public.timesheets t ON t.id = ta.timesheet_id
                WHERE t.company_id = :cid
                  AND t.status IN ('SUBMITTED','UNDER_REVIEW')) AS timesheets,
              (SELECT count(*) FROM public.invoice_approvals ia
                JOIN public.invoices i ON i.id = ia.invoice_id
                WHERE i.company_id = :cid AND i.status IN ('PENDING','SUBMITTED')) AS invoices,
              (SELECT count(*) FROM public.contract_approval_steps cs
                 JOIN public.contracts c ON c.id = cs.contract_id
                WHERE c.company_id = :cid AND cs.status = 'PENDING') AS contracts,
              (SELECT count(*) FROM public.leave_requests lr
                WHERE lr.company_id = :cid AND lr.status = 'PENDING') AS leave
            """
        ),
        {"cid": company_scope(ctx)},
    )
    row: dict[str, Any] = dict(approvals.mappings().first() or {})
    return {
        "notifications": int(notifications.scalar() or 0),
        "approvals": sum(int(v or 0) for v in dict(row).values()),
        "timesheets": int(row.get("timesheets") or 0),
        "invoices": int(row.get("invoices") or 0),
        "contracts": int(row.get("contracts") or 0),
        "leave": int(row.get("leave") or 0),
    }


@router.post("/notifications/read", summary="Mark notifications as read")
async def mark_read(
    ctx_and_conn: NotificationsRead, ids: list[uuid.UUID] | None = None
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    if ids:
        await conn.execute(
            text(
                "UPDATE platform.notifications SET read_at = now()"
                " WHERE user_id = :uid AND id = ANY(CAST(:ids AS uuid[])) AND read_at IS NULL"
            ),
            {"uid": ctx.user_id, "ids": [str(i) for i in ids]},
        )
    else:
        await conn.execute(
            text(
                "UPDATE platform.notifications SET read_at = now()"
                " WHERE user_id = :uid AND read_at IS NULL"
            ),
            {"uid": ctx.user_id},
        )
    return {"ok": True, "request_id": ctx.request_id}


@router.delete("/notifications/{notification_id:uuid}", summary="Delete a notification")
async def delete_notification(
    ctx_and_conn: NotificationsRead, notification_id: uuid.UUID
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    await conn.execute(
        text("DELETE FROM platform.notifications WHERE id = :nid AND user_id = :uid"),
        {"nid": notification_id, "uid": ctx.user_id},
    )
    return {"ok": True, "request_id": ctx.request_id}


# =============================================================================
# dashboard
# =============================================================================
@router.get("/dashboard", summary="Role-aware company dashboard")
async def company_dashboard(ctx_and_conn: DashboardRead) -> dict[str, Any]:
    """Panels the caller may read. A panel they may not read is omitted rather
    than zeroed, so an empty card always means "nothing to show"."""
    ctx, conn = ctx_and_conn
    return await dashboard_service.company_dashboard(
        conn, company_id=company_scope(ctx), user_id=ctx.user_id, permissions=ctx.permissions
    )


# =============================================================================
# AI domain intelligence
# =============================================================================
@router.get("/ai/providers", summary="AI provider availability")
async def ai_providers(ctx_and_conn: AiRead) -> dict[str, Any]:
    """Which providers are live. Never invents capability: a provider with no
    credentials is reported as unavailable."""
    ctx, conn = ctx_and_conn
    return ai_domain.provider_status()


@router.post("/ai/assistant/domain", summary="Ask a question about your business data")
async def domain_assistant(ctx_and_conn: AiAssistant, payload: AssistantBody) -> dict[str, Any]:
    """A permission-aware question over the caller's own data.

    Answers are grounded in a snapshot filtered by the caller's permissions, so
    the assistant can never surface a record they could not open in the UI.
    """
    ctx, conn = ctx_and_conn
    return await ai_domain.assistant(
        conn,
        company_id=company_scope(ctx),
        user_id=ctx.user_id,
        question=payload.question,
        actor_permissions=ctx.permissions,
    )


@router.post("/ai/contract-intelligence", summary="Extract contract terms")
async def contract_intelligence(
    ctx_and_conn: AiContract, payload: ContractFactsBody
) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await ai_domain.extract_contract_facts(
        conn,
        company_id=company_scope(ctx),
        user_id=ctx.user_id,
        contract_public_id=payload.contract_id,
        document_public_id=payload.document_id,
        text_value=payload.text,
    )


@router.post("/ai/sow-intelligence", summary="Compare a SOW with its contract")
async def sow_intelligence(ctx_and_conn: AiContract, payload: SowCompareBody) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await ai_domain.compare_sow_contract(
        conn, company_id=company_scope(ctx), user_id=ctx.user_id, sow_public_id=payload.sow_id
    )


@router.post("/ai/timesheet-intelligence/{timesheet_id}", summary="Explain timesheet anomalies")
async def timesheet_intelligence(ctx_and_conn: AiContract, timesheet_id: str) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await ai_domain.analyse_timesheet(
        conn, company_id=company_scope(ctx), user_id=ctx.user_id, timesheet_public_id=timesheet_id
    )


@router.get("/ai/project-intelligence/{project_id}", summary="Project health and risk signals")
async def project_intelligence(ctx_and_conn: AiProject, project_id: str) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await ai_domain.project_health(
        conn, company_id=company_scope(ctx), project_public_id=project_id
    )


@router.get("/ai/financial-intelligence", summary="Receivables, forecast and anomalies")
async def financial_intelligence(ctx_and_conn: AiFinancial) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await ai_domain.financial_overview(
        conn, company_id=company_scope(ctx), user_id=ctx.user_id
    )


@router.get("/ai/workforce-intelligence", summary="Utilisation and capacity")
async def workforce_intelligence(ctx_and_conn: AiWorkforce) -> dict[str, Any]:
    ctx, conn = ctx_and_conn
    return await ai_domain.workforce_overview(conn, company_id=company_scope(ctx))


@router.get("/ai/insights", response_model=Page[dict[str, Any]], summary="Stored AI insights")
async def list_insights(
    ctx_and_conn: AiRead,
    entity_type: str | None = Query(None, max_length=32),
    severity: str | None = Query(None, max_length=16),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> Page[Any]:
    ctx, conn = ctx_and_conn
    where = ["i.company_id = :cid", "(i.expires_at IS NULL OR i.expires_at > now())"]
    params: dict[str, Any] = {"cid": company_scope(ctx), "limit": limit, "offset": offset}
    if entity_type:
        where.append("i.entity_type = :entity_type")
        params["entity_type"] = entity_type
    if severity:
        where.append("i.severity = :severity")
        params["severity"] = severity

    rows = (
        (
            await conn.execute(
                text(
                    f"""
                    SELECT i.public_id, i.insight_type, i.severity, i.title, i.summary,
                           i.entity_type, i.entity_public_id, i.data, i.confidence,
                           i.created_at, i.expires_at
                      FROM public.ai_insights i
                     WHERE {" AND ".join(where)}
                     ORDER BY i.created_at DESC
                     LIMIT :limit OFFSET :offset
                    """  # noqa: S608
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    return build_page(
        [dict(r) for r in rows], limit=limit, cursor_keys=("created_at",), request_id=ctx.request_id
    )


@router.get("/ai/agents", summary="AI agent catalogue")
async def list_domain_agents(ctx_and_conn: AiRead) -> dict[str, Any]:
    """Agents with the tools each offers, filtered to what the caller may use."""
    ctx, conn = ctx_and_conn
    agents = ai_domain.available_agents()
    for agent in agents:
        agent["available_tools"] = [
            tool for tool in agent["tools"] if ctx.can(tool["required_permission"])
        ]
    return {"data": agents, "request_id": ctx.request_id}


@router.get("/ai/knowledge", response_model=Page[dict[str, Any]], summary="AI knowledge documents")
async def list_knowledge(
    ctx_and_conn: AiRead,
    q: str | None = Query(None, max_length=200),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> Page[Any]:
    ctx, conn = ctx_and_conn
    where = ["k.company_id = :cid"]
    params: dict[str, Any] = {"cid": company_scope(ctx), "limit": limit, "offset": offset}
    if q:
        where.append("(k.title ILIKE :q OR k.source_type ILIKE :q)")
        params["q"] = f"%{q}%"

    rows = (
        (
            await conn.execute(
                text(
                    f"""
                    SELECT k.public_id, k.title, k.source_type, k.status, k.chunk_count,
                           k.token_count, k.language, k.created_at, k.updated_at
                      FROM public.ai_knowledge_documents k
                     WHERE {" AND ".join(where)}
                     ORDER BY k.updated_at DESC
                     LIMIT :limit OFFSET :offset
                    """  # noqa: S608
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    return build_page(
        [dict(r) for r in rows], limit=limit, cursor_keys=("updated_at",), request_id=ctx.request_id
    )


@router.get("/ai/automations", response_model=Page[dict[str, Any]], summary="AI automations")
async def list_automations(
    ctx_and_conn: AiRead,
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> Page[Any]:
    ctx, conn = ctx_and_conn
    rows = (
        (
            await conn.execute(
                text(
                    """
                    SELECT a.public_id, a.name, a.description, a.trigger_type, a.is_active,
                           a.schedule_cron, a.risk_level, a.last_run_at, a.run_count,
                           a.created_at
                      FROM public.ai_automations a
                     WHERE a.company_id = :cid
                     ORDER BY a.created_at DESC
                     LIMIT :limit OFFSET :offset
                    """
                ),
                {"cid": company_scope(ctx), "limit": limit, "offset": offset},
            )
        )
        .mappings()
        .all()
    )
    return build_page(
        [dict(r) for r in rows], limit=limit, cursor_keys=("created_at",), request_id=ctx.request_id
    )


@router.get("/ai/actions", response_model=Page[dict[str, Any]], summary="Proposed AI actions")
async def list_ai_actions(
    ctx_and_conn: AiRead,
    status_filter: str | None = Query(None, alias="status", max_length=32),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> Page[Any]:
    ctx, conn = ctx_and_conn
    rows = (
        (
            await conn.execute(
                text(
                    """
                    SELECT a.public_id, a.agent_key, a.action_type, a.target_type,
                           a.status, a.risk_level, a.required_permission, a.rationale,
                           a.parameters, a.created_at, a.expires_at,
                           u.public_id AS proposed_by
                      FROM public.ai_actions a
                      LEFT JOIN public.users u ON u.id = a.proposed_by
                     WHERE a.company_id = :cid
                       AND (:status IS NULL OR a.status = :status)
                       AND (:uid IS NULL OR a.proposed_by = :uid)
                     ORDER BY a.created_at DESC
                     LIMIT :limit OFFSET :offset
                    """
                ),
                {
                    "cid": company_scope(ctx),
                    "status": status_filter,
                    "uid": None,
                    "limit": limit,
                    "offset": offset,
                },
            )
        )
        .mappings()
        .all()
    )
    return build_page(
        [dict(r) for r in rows], limit=limit, cursor_keys=("created_at",), request_id=ctx.request_id
    )
