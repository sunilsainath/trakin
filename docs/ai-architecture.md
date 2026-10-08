# MyTrakin — AI architecture

Vendor-neutral by construction: `app/ai/gateway.py` (router, budgets, usage
ledger, TTL cache) + `app/ai/providers/{openai,anthropic,gemini,local}.py`.
No vendor SDK outside providers/. Supported: chat + embeddings per provider,
fallback routing, per-user token and per-company cost budgets, `was_cached`
metering (tool-bearing responses are never cached or replayed).

## RAG (`app/ai/rag.py`)

Document → extraction → chunking → embeddings → pgvector →
**permission-first** retrieval (`app.ai_visible_chunks` filters before context
assembly; hiding post-retrieval is forbidden) → answer + citations. Tested
cross-tenant (`test_t12`).

## Capabilities (`app/services/ai_domain.py`)

Contract facts/risks/comparison, timesheet anomaly explanation, invoice
validation (duplicate/amount/rate/period/MSA), project health, workforce and
financial overviews, daily briefing (permission-gated sections). Anomaly
findings use observation language ("Potential anomaly detected"), never
accusations.

## Agents (`app/ai/agents.py`)

Contract, Finance, Project, Compliance, Workforce, Recruitment, Workflow.
Propose → approve → execute with capability tokens; CRITICAL needs a different
approver; tool calls re-check the initiator's permissions. Automations:
deterministic NL→draft compiler (`automation_builder.py`) storing inactive
drafts; financial actions always require approval.

## Safety & audit

Untrusted documents are wrapped before prompting; prompts/system instructions
cannot be overridden by content. Every action, model/version, prompt version,
confidence and approval is logged (`ai_actions`, `ai_usage_events`).
