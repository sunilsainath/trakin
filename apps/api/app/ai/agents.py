"""Agent framework.

An agent is a named bundle of tools plus a policy. Two invariants hold for every
agent regardless of which one runs:

  1. **Tools are re-authorized, not trusted.** Before any tool call is executed,
     `app.has_permission` is consulted for the human who initiated the action.
     The model cannot grant itself a permission it does not have.

  2. **Consequential actions require approval.** A proposed action is written to
     `ai_actions` in PENDING_APPROVAL with a single-use capability token bound to
     the initiating human. A database trigger refuses EXECUTED without that
     token and, for CRITICAL actions, without a different approver.

The agent therefore *proposes*; a human *authorises*; the executor *performs*.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.ai.gateway import ChatRequest, Citation, Message, ToolDefinition
from app.core.errors import (
    AIActionNotApprovedError,
    AIProviderError,
    IntegrationNotConfiguredError,
    PermissionDeniedError,
    ResourceNotFoundError,
)
from app.core.logging import get_logger
from app.services import audit

logger = get_logger(__name__)

RiskLevel = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]

# Actions that always require a human decision, whatever the risk level says.
ALWAYS_APPROVE_ACTIONS = frozenset(
    {
        "initiate_payment",
        "submit_invoice",
        "approve_invoice",
        "send_contract",
        "modify_contract",
        "terminate_contract",
        "change_permissions",
        "assign_role",
        "transfer_funds",
        "delete_document",
    }
)


@dataclass(frozen=True, slots=True)
class AgentTool:
    """A capability an agent may request.

    `required_permission` is checked against the initiating human, not against
    the agent, which has no identity of its own.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    required_permission: str
    risk_level: RiskLevel = "MEDIUM"
    mutating: bool = True
    handler_key: str = ""


@dataclass(slots=True)
class AgentDefinition:
    key: str
    display_name: str
    purpose: str
    tools: list[AgentTool] = field(default_factory=list)
    system_prompt: str = ""
    max_iterations: int = 6


@dataclass(slots=True)
class ProposedAction:
    """What the agent wants to do, before anyone has agreed to it."""

    action_type: str
    target_type: str
    target_id: str | None
    parameters: dict[str, Any]
    rationale: str
    risk_level: RiskLevel
    required_permission: str
    # True when the action would change a record. Carried on the proposal so the
    # approval rule can be evaluated from the stored action alone, rather than
    # from the tool definition that happened to produce it.
    mutating: bool = False
    confidence: float | None = None
    citations: list[Citation] = field(default_factory=list)


@dataclass(slots=True)
class AgentRun:
    agent_key: str
    action_public_id: str
    status: str
    requires_approval: bool
    proposal: dict[str, Any]
    rationale: str
    citations: list[Citation] = field(default_factory=list)


class AgentRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, AgentDefinition] = {}
        self._register_defaults()

    def register(self, agent: AgentDefinition) -> None:
        self._agents[agent.key] = agent

    def get(self, key: str) -> AgentDefinition:
        agent = self._agents.get(key)
        if agent is None:
            raise ResourceNotFoundError("Unknown agent.")
        return agent

    def list(self) -> list[dict[str, Any]]:
        return [
            {
                "key": a.key,
                "display_name": a.display_name,
                "purpose": a.purpose,
                "tools": [
                    {
                        "name": t.name,
                        "description": t.description,
                        "required_permission": t.required_permission,
                        "risk_level": t.risk_level,
                        "mutating": t.mutating,
                    }
                    for t in a.tools
                ],
            }
            for a in sorted(self._agents.values(), key=lambda x: x.key)
        ]

    def _register_defaults(self) -> None:
        self.register(
            AgentDefinition(
                key="contract_agent",
                display_name="Contract Agent",
                purpose=(
                    "Summarises contracts, identifies risk, proposes renegotiation "
                    "points and prepares draft contract changes for human review."
                ),
                system_prompt=(
                    "You analyse contracts. You may propose a contract change, but "
                    "you may never send, activate or modify a contract yourself: "
                    "every such action is recorded as a proposal awaiting a human "
                    "decision. Quote the clause you rely on for each finding."
                ),
                tools=[
                    AgentTool(
                        name="propose_contract_change",
                        description="Propose an edit to contract terms for human review.",
                        parameters={
                            "type": "object",
                            "properties": {
                                "contract_public_id": {"type": "string"},
                                "field": {"type": "string"},
                                "proposed_value": {"type": "string"},
                                "rationale": {"type": "string"},
                            },
                            "required": [
                                "contract_public_id",
                                "field",
                                "proposed_value",
                                "rationale",
                            ],
                        },
                        required_permission="contracts.update",
                        risk_level="HIGH",
                        handler_key="propose_contract_change",
                    ),
                    AgentTool(
                        name="summarise_contract",
                        description="Summarise the commercial terms of a contract.",
                        parameters={
                            "type": "object",
                            "properties": {"contract_public_id": {"type": "string"}},
                            "required": ["contract_public_id"],
                        },
                        required_permission="contracts.read",
                        risk_level="LOW",
                        mutating=False,
                        handler_key="summarise_contract",
                    ),
                ],
            )
        )

        self.register(
            AgentDefinition(
                key="finance_agent",
                display_name="Finance Agent",
                purpose=(
                    "Explains receivables and payables, flags unusual patterns and "
                    "drafts payment plans. It never moves money on its own."
                ),
                system_prompt=(
                    "You analyse financial data. You may propose payments, but a "
                    "payment is only ever created as a proposal that a human must "
                    "approve. State clearly when a figure is a prediction."
                ),
                tools=[
                    AgentTool(
                        name="propose_payment",
                        description="Propose recording or initiating a payment.",
                        parameters={
                            "type": "object",
                            "properties": {
                                "invoice_public_id": {"type": "string"},
                                "amount": {"type": "string"},
                                "rationale": {"type": "string"},
                            },
                            "required": ["invoice_public_id", "amount", "rationale"],
                        },
                        required_permission="payments.create",
                        risk_level="CRITICAL",
                        handler_key="propose_payment",
                    ),
                    AgentTool(
                        name="explain_ar_aging",
                        description="Explain the current AR aging profile.",
                        parameters={"type": "object", "properties": {}},
                        required_permission="invoices.read",
                        risk_level="LOW",
                        mutating=False,
                        handler_key="explain_ar_aging",
                    ),
                ],
            )
        )

        self.register(
            AgentDefinition(
                key="project_agent",
                display_name="Project Agent",
                purpose=(
                    "Assesses project health across schedule, budget, staffing and "
                    "billing, and proposes corrective actions."
                ),
                system_prompt=(
                    "You assess delivery risk. Base every conclusion on the data "
                    "provided. Distinguish observed data from your inference."
                ),
                tools=[
                    AgentTool(
                        name="assess_project_health",
                        description="Produce a health assessment for a project.",
                        parameters={
                            "type": "object",
                            "properties": {"project_public_id": {"type": "string"}},
                            "required": ["project_public_id"],
                        },
                        required_permission="projects.read",
                        risk_level="LOW",
                        mutating=False,
                        handler_key="assess_project_health",
                    )
                ],
            )
        )

        self.register(
            AgentDefinition(
                key="compliance_agent",
                display_name="Compliance Agent",
                purpose=(
                    "Tracks document expiry, insurance, certifications and required "
                    "approvals, and raises alerts before a deadline is missed."
                ),
                system_prompt=(
                    "You monitor compliance obligations. You report potential "
                    "issues as observations, never as accusations about a person or "
                    "company."
                ),
                tools=[
                    AgentTool(
                        name="list_compliance_gaps",
                        description="List documents or approvals that are expiring or missing.",
                        parameters={"type": "object", "properties": {}},
                        required_permission="documents.read",
                        risk_level="LOW",
                        mutating=False,
                        handler_key="list_compliance_gaps",
                    )
                ],
            )
        )

        self.register(
            AgentDefinition(
                key="workforce_agent",
                display_name="Workforce Agent",
                purpose=(
                    "Matches available people to role requirements and identifies "
                    "skill gaps. It never ranks candidates on protected attributes."
                ),
                system_prompt=(
                    "You match people to roles using professional attributes only: "
                    "skills, experience, availability, rate and location. Never use "
                    "or infer age, gender, ethnicity, religion, disability, "
                    "pregnancy or any similar protected characteristic."
                ),
                tools=[
                    AgentTool(
                        name="find_candidates",
                        description="Find available professionals matching a role.",
                        parameters={
                            "type": "object",
                            "properties": {
                                "role_title": {"type": "string"},
                                "max_hourly_rate": {"type": "string"},
                                "skills": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["role_title"],
                        },
                        required_permission="workforce.read",
                        risk_level="LOW",
                        mutating=False,
                        handler_key="find_candidates",
                    )
                ],
            )
        )

        self.register(
            AgentDefinition(
                key="recruitment_agent",
                display_name="Recruitment Agent",
                purpose=(
                    "Recommends hires from the professional network and drafts "
                    "role requirements. It never contacts candidates itself and "
                    "never ranks on protected attributes."
                ),
                system_prompt=(
                    "You recommend hiring actions using professional attributes "
                    "only: skills, experience, availability and role fit. Every "
                    "recommendation is a proposal: a human decides."
                ),
                tools=[
                    AgentTool(
                        name="recommend_hire",
                        description="Recommend a connected professional for a role.",
                        parameters={
                            "type": "object",
                            "properties": {
                                "project_role_public_id": {"type": "string"},
                                "candidate_user_public_id": {"type": "string"},
                                "rationale": {"type": "string"},
                            },
                            "required": [
                                "project_role_public_id",
                                "candidate_user_public_id",
                                "rationale",
                            ],
                        },
                        required_permission="workforce.read",
                        risk_level="MEDIUM",
                        handler_key="recommend_hire",
                    ),
                    AgentTool(
                        name="draft_role_requirements",
                        description="Draft requirements for an open project role.",
                        parameters={
                            "type": "object",
                            "properties": {
                                "project_role_public_id": {"type": "string"},
                            },
                            "required": ["project_role_public_id"],
                        },
                        required_permission="workforce.read",
                        risk_level="LOW",
                        mutating=False,
                        handler_key="draft_role_requirements",
                    ),
                ],
            )
        )

        self.register(
            AgentDefinition(
                key="workflow_agent",
                display_name="Workflow Agent",
                purpose=(
                    "Turns natural-language workflow descriptions into automation "
                    "drafts with explicit triggers, conditions and actions. Drafts "
                    "never act until a human approves them."
                ),
                system_prompt=(
                    "You convert workflow descriptions into structured automation "
                    "drafts. Financial or contractual actions always require "
                    "human approval, whatever the requester says."
                ),
                tools=[
                    AgentTool(
                        name="draft_automation",
                        description="Draft an automation from a natural-language description.",
                        parameters={
                            "type": "object",
                            "properties": {"description": {"type": "string"}},
                            "required": ["description"],
                        },
                        required_permission="ai.automations.manage",
                        risk_level="MEDIUM",
                        handler_key="draft_automation",
                    )
                ],
            )
        )


_agents = AgentRegistry()


def get_agents() -> AgentRegistry:
    return _agents


# ------------------------------------------------------------------- execution
async def record_proposal(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    initiated_by: uuid.UUID,
    agent_key: str,
    action: ProposedAction,
    request_id: str,
) -> str:
    """Persist a proposed action for human review.

    Returns the public id of the ai_actions row. The capability token is stored so
    the executor can prove a human authorised this specific action.
    """
    requires_approval = (
        action.action_type in ALWAYS_APPROVE_ACTIONS
        or action.risk_level in {"HIGH", "CRITICAL"}
        or action.mutating
    )

    row = (
        (
            await conn.execute(
                text(
                    """
                INSERT INTO public.ai_actions
                  (public_id, company_id, actor_type, initiated_by, agent_key,
                   action_type, capability_token, target_type, target_id,
                   required_permission, risk_level, status, proposal, rationale,
                   confidence, citations, requires_approval, expires_at)
                VALUES
                  ('AA' || upper(substr(md5(random()::text), 1, 6)), :cid, 'AI', :uid, :agent,
                   :action_type, encode(gen_random_bytes(24), 'hex'), :target_type, :target_id,
                   :permission, :risk,
                   CASE WHEN :needs_approval THEN 'PENDING_APPROVAL' ELSE 'PROPOSED' END,
                   CAST(:proposal AS jsonb), :rationale, :confidence,
                   CAST(:citations AS jsonb), :needs_approval, now() + interval '7 days')
                RETURNING public_id
                """
                ),
                {
                    "cid": company_id,
                    "uid": initiated_by,
                    "agent": agent_key,
                    "action_type": action.action_type,
                    "target_type": action.target_type,
                    "target_id": action.target_id,
                    "permission": action.required_permission,
                    "risk": action.risk_level,
                    "needs_approval": requires_approval,
                    "proposal": json.dumps(action.parameters, default=str),
                    "rationale": action.rationale,
                    "confidence": action.confidence,
                    "citations": json.dumps(
                        [
                            {
                                "document_public_id": c.document_public_id,
                                "title": c.title,
                                "page_number": c.page_number,
                            }
                            for c in action.citations
                        ]
                    ),
                },
            )
        )
        .mappings()
        .first()
    )

    if row is None:
        # The INSERT ... RETURNING above guarantees a row, so this can only mean
        # the statement did not run as written. Failing loudly beats indexing None.
        raise AIProviderError("The proposed action could not be recorded.")

    await audit.record(
        conn,
        action="ai.action_proposed",
        resource_type="ai_action",
        resource_id=None,
        resource_public_id=str(row["public_id"]),
        company_id=company_id,
        actor_user_id=initiated_by,
        actor_type="AI",
        actor_label=agent_key,
        new_values={
            "action_type": action.action_type,
            "risk_level": action.risk_level,
            "requires_approval": requires_approval,
        },
        request_id=request_id,
    )

    return str(row["public_id"])


async def approve(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    action_public_id: str,
    approver_id: uuid.UUID,
    permission: str,
    request_id: str,
    notes: str | None = None,
) -> dict[str, Any]:
    """Record a human decision on a proposed action."""
    held = (
        await conn.execute(
            text("SELECT app.has_permission(:cid, :perm, :uid) AS ok"),
            {"cid": company_id, "perm": permission, "uid": approver_id},
        )
    ).scalar()

    if not held:
        raise PermissionDeniedError(f"Approving this action requires the {permission} permission.")

    row = (
        (
            await conn.execute(
                text(
                    """
                UPDATE public.ai_actions
                   SET status = 'APPROVED', approved_by = :uid, approved_at = now()
                 WHERE company_id = :cid
                   AND public_id = :pid
                   AND status IN ('PROPOSED', 'PENDING_APPROVAL')
                RETURNING id::text, action_type, risk_level
                """
                ),
                {"cid": company_id, "pid": action_public_id, "uid": approver_id},
            )
        )
        .mappings()
        .first()
    )

    if row is None:
        raise ResourceNotFoundError("No pending action with that identifier.")

    await audit.record(
        conn,
        action="ai.action_approved",
        resource_type="ai_action",
        resource_id=uuid.UUID(str(row["id"])),
        resource_public_id=action_public_id,
        company_id=company_id,
        actor_user_id=approver_id,
        old_values={"status": "PENDING_APPROVAL"},
        new_values={"status": "APPROVED"},
        reason=notes,
        request_id=request_id,
    )
    return {"public_id": action_public_id, "status": "APPROVED", "action_type": row["action_type"]}


async def reject(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    action_public_id: str,
    approver_id: uuid.UUID,
    request_id: str,
    reason: str,
) -> dict[str, Any]:
    row = (
        (
            await conn.execute(
                text(
                    """
                UPDATE public.ai_actions
                   SET status = 'REJECTED', approved_by = :uid, approved_at = now(),
                       error_message = :reason
                 WHERE company_id = :cid AND public_id = :pid
                   AND status IN ('PROPOSED', 'PENDING_APPROVAL')
                RETURNING id::text
                """
                ),
                {"cid": company_id, "pid": action_public_id, "uid": approver_id, "reason": reason},
            )
        )
        .mappings()
        .first()
    )

    if row is None:
        raise ResourceNotFoundError("No pending action with that identifier.")

    await audit.record(
        conn,
        action="ai.action_rejected",
        resource_type="ai_action",
        resource_id=uuid.UUID(str(row["id"])),
        resource_public_id=action_public_id,
        company_id=company_id,
        actor_user_id=approver_id,
        new_values={"status": "REJECTED"},
        reason=reason,
        request_id=request_id,
    )
    return {"public_id": action_public_id, "status": "REJECTED"}


async def execute(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    action_public_id: str,
    executor_id: uuid.UUID,
    request_id: str,
) -> dict[str, Any]:
    """Mark an approved action as executed.

    The preconditions are enforced by a database trigger, not only here: approval
    recorded, the required permission held, a capability token present, and for
    CRITICAL actions a different approver. An agent therefore cannot skip this
    gate by calling a different code path.
    """
    row = (
        (
            await conn.execute(
                text(
                    """
                SELECT id::text, action_type, required_permission, status,
                       capability_token
                  FROM public.ai_actions
                 WHERE company_id = :cid AND public_id = :pid
                """
                ),
                {"cid": company_id, "pid": action_public_id},
            )
        )
        .mappings()
        .first()
    )

    if row is None:
        raise ResourceNotFoundError("Action not found.")

    if row["status"] != "APPROVED":
        raise AIActionNotApprovedError(
            f"This action is {row['status'].lower()} and cannot be executed."
        )

    if row["capability_token"] is None:
        raise AIActionNotApprovedError(
            "This action has no capability token bound to a human approver."
        )

    permission = row["required_permission"]
    held = (
        await conn.execute(
            text("SELECT app.has_permission(:cid, :perm, :uid) AS ok"),
            {"cid": company_id, "perm": permission, "uid": executor_id},
        )
    ).scalar()

    if not held:
        raise PermissionDeniedError(f"Executing this action requires the {permission} permission.")

    try:
        await conn.execute(
            text(
                """
                UPDATE public.ai_actions
                   SET status = 'EXECUTED', executed_at = now()
                 WHERE id = CAST(:id AS uuid)
                """
            ),
            {"id": row["id"]},
        )
    except Exception as exc:
        logger.warning("ai_action_execution_blocked", error=str(exc)[:200])
        raise AIActionNotApprovedError(str(exc)[:200]) from exc

    await audit.record(
        conn,
        action="ai.action_executed",
        resource_type="ai_action",
        resource_id=uuid.UUID(str(row["id"])),
        resource_public_id=action_public_id,
        company_id=company_id,
        actor_user_id=executor_id,
        actor_type="AGENT",
        actor_label=str(row["action_type"]),
        new_values={"status": "EXECUTED"},
        request_id=request_id,
    )

    return {
        "public_id": action_public_id,
        "status": "EXECUTED",
        "action_type": str(row["action_type"]),
    }


async def plan(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    user_id: uuid.UUID,
    agent_key: str,
    objective: str,
    context_text: str = "",
    citations: list[Citation] | None = None,
    request_id: str = "",
) -> AgentRun:
    """Ask an agent to propose (not perform) a next action.

    The model's tool calls are converted into ProposedAction records. Nothing is
    executed here; a separate approve + execute pair is required.
    """
    from app.ai.gateway import get_gateway

    agent = get_agents().get(agent_key)

    # Only offer tools the initiating human is actually allowed to request.
    available: list[ToolDefinition] = []
    for agent_tool in agent.tools:
        if agent_tool.mutating:
            held = (
                await conn.execute(
                    text("SELECT app.has_permission(:cid, :perm, :uid) AS ok"),
                    {
                        "cid": company_id,
                        "perm": agent_tool.required_permission,
                        "uid": user_id,
                    },
                )
            ).scalar()
            if not held:
                logger.info(
                    "agent_tool_hidden",
                    agent=agent_key,
                    tool=agent_tool.name,
                    reason="caller lacks permission",
                )
                continue
        available.append(
            ToolDefinition(
                name=agent_tool.name,
                description=agent_tool.description,
                parameters=agent_tool.parameters,
                required_permission=agent_tool.required_permission,
                risk_level=agent_tool.risk_level,
                mutating=agent_tool.mutating,
            )
        )

    messages: list[Message] = [Message("system", agent.system_prompt)]
    if context_text:
        messages.append(Message("user", f"CONTEXT\n{context_text}\n\nOBJECTIVE\n{objective}"))
    else:
        messages.append(Message("user", objective))

    try:
        response = await get_gateway().chat(
            ChatRequest(
                messages=messages,
                tools=available,
                feature=f"agent:{agent_key}",
                company_id=company_id,
                user_id=user_id,
                # The field is `context`, not `citations`: it is the
                # pre-retrieved, permission-filtered evidence the model may cite.
                context=citations or [],
            )
        )
    except IntegrationNotConfiguredError:
        raise
    except AIProviderError as exc:
        raise AIProviderError("The agent could not complete its analysis.") from exc

    if not response.tool_calls:
        # No action proposed: the agent answered analytically.
        return AgentRun(
            agent_key=agent_key,
            action_public_id="",
            status="ANSWERED",
            requires_approval=False,
            proposal={"analysis": response.content},
            rationale=response.content[:2000],
            citations=response.citations,
        )

    call = response.tool_calls[0]
    tool_name = str(call.get("name") or (call.get("function") or {}).get("name") or "")
    arguments = call.get("input") or call.get("arguments") or {}
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            arguments = {}

    # An unknown tool name is a model failure, not an authorisation question, so
    # it is answered rather than raised: nothing is proposed and nothing executes.
    tool: AgentTool | None = next((t for t in agent.tools if t.name == tool_name), None)
    if tool is None:
        return AgentRun(
            agent_key=agent_key,
            action_public_id="",
            status="ANSWERED",
            requires_approval=False,
            proposal={"analysis": response.content},
            rationale="The model requested an unknown tool; nothing was proposed.",
            citations=response.citations,
        )

    proposed = ProposedAction(
        action_type=tool.handler_key or tool_name,
        target_type=str(
            arguments.get("contract_public_id")
            or arguments.get("invoice_public_id")
            or arguments.get("project_public_id")
            or ""
        ),
        target_id=None,
        parameters=dict(arguments),
        rationale=str(arguments.get("rationale") or response.content[:1000]),
        risk_level=tool.risk_level,
        required_permission=tool.required_permission,
        mutating=tool.mutating,
        confidence=None,
        citations=response.citations,
    )

    public_id = await record_proposal(
        conn,
        company_id=company_id,
        initiated_by=user_id,
        agent_key=agent_key,
        action=proposed,
        request_id=request_id,
    )

    status = (
        await conn.execute(
            text("SELECT status FROM public.ai_actions WHERE public_id = :pid"),
            {"pid": public_id},
        )
    ).scalar() or "PROPOSED"

    return AgentRun(
        agent_key=agent_key,
        action_public_id=public_id,
        status=str(status),
        requires_approval=tool.mutating or tool.risk_level in {"HIGH", "CRITICAL"},
        proposal=dict(arguments),
        rationale=proposed.rationale,
        citations=response.citations,
    )
