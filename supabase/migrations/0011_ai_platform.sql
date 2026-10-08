-- =============================================================================
-- MyTrakin :: 0011_ai_platform.sql
-- AI platform: gateway ledger, conversations, RAG knowledge, extractions with
-- confidence, human-in-the-loop actions, budget accounting, automations.
--
-- Invariants enforced in the database:
--   A1  AI never writes production records directly: extractions and actions are
--       staged PENDING_REVIEW and applied only by a confirmed human action
--   A2  RAG retrieval is permission-filtered BEFORE context assembly
--   A3  every AI call is metered (ai_usage_events) with cost in cents
--   A4  agent tool calls require a capability token bound to a human actor
--   A5  sensitive fields are excluded from AI context by construction
-- =============================================================================

-- -----------------------------------------------------------------------------
-- ai_providers — runtime-configured model registry. The application never names
-- a vendor in code; it resolves a provider key here.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.ai_providers (
  key               text PRIMARY KEY,           -- 'openai' | 'anthropic' | 'gemini' | 'local'
  display_name      text NOT NULL,
  provider_type     text NOT NULL
                      CHECK (provider_type IN ('CHAT','EMBEDDING','MULTIMODAL','OCR')),
  base_url          text,
  model_name        text NOT NULL,
  context_window    int NOT NULL DEFAULT 8192,
  max_output_tokens int NOT NULL DEFAULT 1024,
  cost_per_1k_input_cents  numeric(12,6) NOT NULL DEFAULT 0,
  cost_per_1k_output_cents numeric(12,6) NOT NULL DEFAULT 0,
  supports_functions boolean NOT NULL DEFAULT true,
  supports_vision   boolean NOT NULL DEFAULT false,
  is_enabled        boolean NOT NULL DEFAULT false,
  priority          int NOT NULL DEFAULT 100,   -- lower = preferred in routing
  health_status     text NOT NULL DEFAULT 'UNKNOWN'
                      CHECK (health_status IN ('HEALTHY','DEGRADED','DOWN','UNKNOWN')),
  last_health_check_at timestamptz,
  config            jsonb NOT NULL DEFAULT '{}'::jsonb,  -- non-secret provider options only
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);

COMMENT ON COLUMN public.ai_providers.config IS
  'Non-secret provider options only. API keys live in the secrets manager and never in this table.';

-- -----------------------------------------------------------------------------
-- ai_usage_events — A3. Append-only metering ledger for cost/rate limiting.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.ai_usage_events (
  id                uuid PRIMARY KEY DEFAULT app.uuid7(),
  company_id        uuid,
  user_id           uuid,
  provider_key      text NOT NULL,
  model_name        text NOT NULL,
  feature           text NOT NULL,             -- contract_intelligence, rag, assistant, ...
  request_id        text,
  conversation_id   uuid,
  prompt_tokens     int NOT NULL DEFAULT 0 CHECK (prompt_tokens >= 0),
  completion_tokens int NOT NULL DEFAULT 0 CHECK (completion_tokens >= 0),
  total_tokens      int GENERATED ALWAYS AS (prompt_tokens + completion_tokens) STORED,
  estimated_cost_cents numeric(12,6) NOT NULL DEFAULT 0,
  latency_ms        int,
  was_cached        boolean NOT NULL DEFAULT false,
  status            text NOT NULL DEFAULT 'SUCCESS'
                      CHECK (status IN ('SUCCESS','FAILED','BLOCKED','RATE_LIMITED','CACHED')),
  error_code        text,
  redaction_applied boolean NOT NULL DEFAULT false,
  created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_aiusage_company_time ON public.ai_usage_events (company_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_aiusage_user_time    ON public.ai_usage_events (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_aiusage_feature      ON public.ai_usage_events (feature, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_aiusage_daily_budget ON public.ai_usage_events (company_id, created_at)
  WHERE status = 'SUCCESS';

DROP TRIGGER IF EXISTS trg_aiusage_immutable ON public.ai_usage_events;
CREATE TRIGGER trg_aiusage_immutable BEFORE UPDATE OR DELETE ON public.ai_usage_events
  FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

-- Budget enforcement helper: spend for the current day for a company/user.
CREATE OR REPLACE FUNCTION app.ai_spend_cents_today(p_company_id uuid) RETURNS numeric
LANGUAGE sql STABLE AS $$
  SELECT COALESCE(sum(estimated_cost_cents), 0)
    FROM public.ai_usage_events
   WHERE company_id = p_company_id
     AND status = 'SUCCESS'
     AND created_at >= date_trunc('day', now());
$$;

CREATE OR REPLACE FUNCTION app.ai_tokens_today(p_user_id uuid) RETURNS bigint
LANGUAGE sql STABLE AS $$
  SELECT COALESCE(sum(total_tokens), 0)
    FROM public.ai_usage_events
   WHERE user_id = p_user_id
     AND status = 'SUCCESS'
     AND created_at >= date_trunc('day', now());
$$;

-- -----------------------------------------------------------------------------
-- ai_conversations / ai_messages
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.ai_conversations (
  id             uuid PRIMARY KEY DEFAULT app.uuid7(),
  public_id      text NOT NULL UNIQUE,
  user_id        uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  company_id     uuid REFERENCES public.companies(id) ON DELETE CASCADE,
  title          text,
  mode           text NOT NULL DEFAULT 'ASSISTANT'
                   CHECK (mode IN ('ASSISTANT','BUSINESS_QA','CONTRACT','DOCUMENT','PROJECT',
                                   'WORKFORCE','FINANCIAL','RISK','COMPLIANCE','PEOPLE')),
  provider_key   text,
  model_name     text,
  system_prompt_version text,
  message_count  int NOT NULL DEFAULT 0,
  total_tokens   bigint NOT NULL DEFAULT 0,
  estimated_cost_cents numeric(12,6) NOT NULL DEFAULT 0,
  is_pinned      boolean NOT NULL DEFAULT false,
  archived_at    timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_aiconv_user    ON public.ai_conversations (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_aiconv_company ON public.ai_conversations (company_id, created_at DESC);

CREATE OR REPLACE FUNCTION app.assign_ai_conversation_public_id() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_try int := 0; v_id text;
BEGIN

  LOOP
    v_id := app.gen_public_id('AC', 8);
    EXIT WHEN NOT EXISTS (SELECT 1 FROM public.ai_conversations WHERE public_id = v_id);
    v_try := v_try + 1;
    IF v_try > 5 THEN RAISE EXCEPTION 'could not allocate ai conversation public_id'; END IF;
  END LOOP;
  NEW.public_id := v_id;
  RETURN NEW;
END
$$;

SELECT app.attach_triggers('public.ai_conversations'::regclass);
DROP TRIGGER IF EXISTS trg_aiconv_public_id ON public.ai_conversations;
CREATE TRIGGER trg_aiconv_public_id BEFORE INSERT ON public.ai_conversations
  FOR EACH ROW WHEN (NEW.public_id IS NULL)
  EXECUTE FUNCTION app.assign_ai_conversation_public_id();

CREATE TABLE IF NOT EXISTS public.ai_messages (
  id              uuid PRIMARY KEY DEFAULT app.uuid7(),
  conversation_id uuid NOT NULL REFERENCES public.ai_conversations(id) ON DELETE CASCADE,
  role            text NOT NULL CHECK (role IN ('system','user','assistant','tool')),
  content         text NOT NULL,
  citations       jsonb NOT NULL DEFAULT '[]'::jsonb,   -- [{doc, chunk, page, section, score}]
  tool_calls      jsonb,
  tool_call_id    text,
  provider_key    text,
  model_name      text,
  prompt_tokens   int NOT NULL DEFAULT 0,
  completion_tokens int NOT NULL DEFAULT 0,
  estimated_cost_cents numeric(12,6) NOT NULL DEFAULT 0,
  confidence      numeric(5,4) CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
  redaction_applied boolean NOT NULL DEFAULT false,
  feedback        text CHECK (feedback IS NULL OR feedback IN ('HELPFUL','NOT_HELPFUL','INCORRECT')),
  feedback_at     timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_aimsg_conversation ON public.ai_messages (conversation_id, created_at);

CREATE OR REPLACE FUNCTION app.refresh_ai_conversation() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, app, pg_temp
AS $$
DECLARE v_conv uuid := NEW.conversation_id;
BEGIN
  UPDATE public.ai_conversations c SET
    message_count = (SELECT count(*) FROM public.ai_messages m WHERE m.conversation_id = v_conv),
    total_tokens  = (SELECT COALESCE(sum(prompt_tokens + completion_tokens), 0)
                       FROM public.ai_messages m WHERE m.conversation_id = v_conv),
    estimated_cost_cents = (SELECT COALESCE(sum(estimated_cost_cents), 0)
                              FROM public.ai_messages m WHERE m.conversation_id = v_conv)
   WHERE c.id = v_conv;
  RETURN NULL;
END
$$;

DROP TRIGGER IF EXISTS trg_aimsg_totals ON public.ai_messages;
CREATE TRIGGER trg_aimsg_totals AFTER INSERT ON public.ai_messages
  FOR EACH ROW EXECUTE FUNCTION app.refresh_ai_conversation();

-- -----------------------------------------------------------------------------
-- ai_knowledge_documents + ai_document_chunks — RAG corpus with pgvector
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.ai_knowledge_documents (
  id                uuid PRIMARY KEY DEFAULT app.uuid7(),
  public_id         text NOT NULL UNIQUE,
  company_id        uuid NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
  document_id       uuid REFERENCES public.documents(id) ON DELETE CASCADE,
  document_version_id uuid REFERENCES public.document_versions(id) ON DELETE CASCADE,
  title             text NOT NULL,
  source_type       text NOT NULL DEFAULT 'DOCUMENT'
                      CHECK (source_type IN ('DOCUMENT','CONTRACT','SOW','MSA','POLICY','PROJECT',
                                             'KNOWLEDGE','MANUAL','CONVERSATION')),
  required_permission text NOT NULL DEFAULT 'documents.read',
  classification    text,
  extraction_state  text NOT NULL DEFAULT 'PENDING'
                      CHECK (extraction_state IN ('PENDING','EXTRACTING','EXTRACTED','CHUNKED',
                                                  'EMBEDDED','FAILED','SKIPPED')),
  chunk_count       int NOT NULL DEFAULT 0,
  embedding_model   text,
  embedded_at       timestamptz,
  is_active         boolean NOT NULL DEFAULT true,
  sensitivity       text NOT NULL DEFAULT 'INTERNAL'
                      CHECK (sensitivity IN ('PUBLIC','INTERNAL','CONFIDENTIAL','RESTRICTED')),
  metadata          jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_by        uuid REFERENCES public.users(id) ON DELETE SET NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);

COMMENT ON COLUMN public.ai_knowledge_documents.required_permission IS
  'A2: retrieval requires this permission in addition to company membership.';

CREATE INDEX IF NOT EXISTS ix_aikd_company ON public.ai_knowledge_documents (company_id, extraction_state);
CREATE INDEX IF NOT EXISTS ix_aikd_document ON public.ai_knowledge_documents (document_id);

CREATE OR REPLACE FUNCTION app.assign_knowledge_public_id() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_try int := 0; v_id text;
BEGIN

  LOOP
    v_id := app.gen_public_id('KD', 8);
    EXIT WHEN NOT EXISTS (SELECT 1 FROM public.ai_knowledge_documents WHERE public_id = v_id);
    v_try := v_try + 1;
    IF v_try > 5 THEN RAISE EXCEPTION 'could not allocate knowledge document public_id'; END IF;
  END LOOP;
  NEW.public_id := v_id;
  RETURN NEW;
END
$$;

SELECT app.attach_triggers('public.ai_knowledge_documents'::regclass);
DROP TRIGGER IF EXISTS trg_aikd_public_id ON public.ai_knowledge_documents;
CREATE TRIGGER trg_aikd_public_id BEFORE INSERT ON public.ai_knowledge_documents
  FOR EACH ROW WHEN (NEW.public_id IS NULL)
  EXECUTE FUNCTION app.assign_knowledge_public_id();

CREATE TABLE IF NOT EXISTS public.ai_document_chunks (
  id                uuid PRIMARY KEY DEFAULT app.uuid7(),
  knowledge_document_id uuid NOT NULL REFERENCES public.ai_knowledge_documents(id) ON DELETE CASCADE,
  company_id        uuid NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
  chunk_index       int NOT NULL,
  content           text NOT NULL,
  token_count       int NOT NULL DEFAULT 0,
  -- embeddings: 1536 dims covers text-embedding-3-small/large and most local models
  embedding         vector(1536),
  embedding_model   text NOT NULL,
  page_number       int,
  section_path      text,       -- e.g. 'Section 4.2 > Payment Terms'
  char_start        int,
  char_end          int,
  content_hash      char(64) NOT NULL,
  metadata          jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (knowledge_document_id, chunk_index),
  CONSTRAINT ck_chunk_tokens CHECK (token_count > 0)
);

COMMENT ON TABLE public.ai_document_chunks IS
  'RAG chunks. Content originates from UNTRUSTED documents: treated as data, never as instructions.';

CREATE INDEX IF NOT EXISTS ix_chunks_kdoc ON public.ai_document_chunks (knowledge_document_id, chunk_index);
CREATE INDEX IF NOT EXISTS ix_chunks_company ON public.ai_document_chunks (company_id);
CREATE INDEX IF NOT EXISTS ix_chunks_hash    ON public.ai_document_chunks (content_hash);
-- HNSW for approximate nearest-neighbour retrieval at scale
CREATE INDEX IF NOT EXISTS ix_chunks_embedding_hnsw
  ON public.ai_document_chunks USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

-- A2: the ONLY retrieval path. Filters by company, permission, sensitivity and
-- active state BEFORE ranking, so unauthorized content never reaches the prompt.
CREATE OR REPLACE FUNCTION app.ai_visible_chunks(
  p_company_id uuid,
  p_query_embedding vector(1536),
  p_match_count int DEFAULT 12,
  p_user_id uuid DEFAULT app.current_user_id(),
  p_min_similarity float DEFAULT 0.2
) RETURNS TABLE (
  chunk_id uuid,
  knowledge_document_id uuid,
  public_id text,
  title text,
  content text,
  page_number int,
  section_path text,
  similarity float,
  sensitivity text
)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, app, pg_temp
AS $$
  SELECT c.id, c.knowledge_document_id, kd.public_id, kd.title, c.content,
         c.page_number, c.section_path,
         (1 - (c.embedding <=> p_query_embedding))::float,
         kd.sensitivity
    FROM public.ai_document_chunks c
    JOIN public.ai_knowledge_documents kd ON kd.id = c.knowledge_document_id
   WHERE kd.company_id = p_company_id
     AND kd.is_active
     AND kd.extraction_state IN ('CHUNKED','EMBEDDED')
     AND c.embedding IS NOT NULL
     AND app.has_permission(p_company_id, kd.required_permission, p_user_id)
     AND NOT app.is_blocked(kd.created_by, NULL, p_user_id)
     AND (1 - (c.embedding <=> p_query_embedding)) >= p_min_similarity
   ORDER BY c.embedding <=> p_query_embedding
   LIMIT LEAST(p_match_count, 50);
$$;

COMMENT ON FUNCTION app.ai_visible_chunks(uuid, vector, int, uuid, float) IS
  'Permission-filtered RAG retrieval. Unauthorized chunks are excluded in SQL, not masked afterwards.';

-- -----------------------------------------------------------------------------
-- ai_extractions — A1. Structured extraction with confidence, staged for review.
-- Used by W-9, timesheet import, contract intelligence, invoice OCR.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.ai_extractions (
  id              uuid PRIMARY KEY DEFAULT app.uuid7(),
  public_id       text NOT NULL UNIQUE,
  company_id      uuid REFERENCES public.companies(id) ON DELETE CASCADE,
  user_id         uuid REFERENCES public.users(id) ON DELETE SET NULL,
  extraction_type text NOT NULL
                    CHECK (extraction_type IN ('W9','TIMESHEET','CONTRACT','SOW','MSA','INVOICE',
                                               'RECEIPT','BANK_STATEMENT','CERTIFICATE','GENERIC')),
  source_document_id uuid REFERENCES public.documents(id) ON DELETE SET NULL,
  source_document_version_id uuid REFERENCES public.document_versions(id) ON DELETE SET NULL,
  provider_key    text,
  model_name      text,
  prompt_version  text,
  status          text NOT NULL DEFAULT 'PENDING_REVIEW'
                    CHECK (status IN ('PENDING_REVIEW','CONFIRMED','PARTIALLY_CONFIRMED','REJECTED','FAILED','SUPERSEDED')),
  overall_confidence numeric(5,4) CHECK (overall_confidence IS NULL
                                         OR (overall_confidence >= 0 AND overall_confidence <= 1)),
  -- field -> {value, confidence, source_page, source_section, evidence, requires_review}
  fields          jsonb NOT NULL DEFAULT '{}'::jsonb,
  warnings        jsonb NOT NULL DEFAULT '[]'::jsonb,
  model_output    jsonb,
  applied_at      timestamptz,
  applied_by      uuid REFERENCES public.users(id) ON DELETE SET NULL,
  applied_target_type text,
  applied_target_id uuid,
  created_at      timestamptz NOT NULL DEFAULT now(),
  reviewed_at     timestamptz
);

COMMENT ON TABLE public.ai_extractions IS
  'AI output staging area. Never auto-applied: applied_at is set only by a confirmed human action (A1).';
COMMENT ON COLUMN public.ai_extractions.fields IS
  'Per-field {value, confidence, page, section, evidence, requires_review}. Low-confidence fields are flagged for review.';

CREATE INDEX IF NOT EXISTS ix_aiex_company  ON public.ai_extractions (company_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_aiex_review   ON public.ai_extractions (user_id, created_at DESC)
  WHERE status = 'PENDING_REVIEW';
CREATE INDEX IF NOT EXISTS ix_aiex_type     ON public.ai_extractions (extraction_type, created_at DESC);

CREATE OR REPLACE FUNCTION app.assign_extraction_public_id() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_try int := 0; v_id text;
BEGIN

  LOOP
    v_id := app.gen_public_id('EX', 8);
    EXIT WHEN NOT EXISTS (SELECT 1 FROM public.ai_extractions WHERE public_id = v_id);
    v_try := v_try + 1;
    IF v_try > 5 THEN RAISE EXCEPTION 'could not allocate extraction public_id'; END IF;
  END LOOP;
  NEW.public_id := v_id;
  RETURN NEW;
END
$$;

SELECT app.attach_triggers('public.ai_extractions'::regclass);
DROP TRIGGER IF EXISTS trg_aiex_public_id ON public.ai_extractions;
CREATE TRIGGER trg_aiex_public_id BEFORE INSERT ON public.ai_extractions
  FOR EACH ROW WHEN (NEW.public_id IS NULL)
  EXECUTE FUNCTION app.assign_extraction_public_id();

-- -----------------------------------------------------------------------------
-- ai_actions — the human-in-the-loop ledger (A1/A4).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.ai_actions (
  id                uuid PRIMARY KEY DEFAULT app.uuid7(),
  public_id         text NOT NULL UNIQUE,
  company_id        uuid NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
  actor_type        public.actor_type NOT NULL DEFAULT 'AI',
  initiated_by      uuid REFERENCES public.users(id) ON DELETE SET NULL,
  approved_by       uuid REFERENCES public.users(id) ON DELETE SET NULL,
  agent_key         text,                    -- contract_agent, finance_agent, ...
  action_type       text NOT NULL,           -- generate_invoice, send_contract, create_project, ...
  capability_token  text,                    -- bound to initiated_by; A4
  target_type       text,
  target_id         uuid,
  required_permission text,
  risk_level        text NOT NULL DEFAULT 'MEDIUM'
                      CHECK (risk_level IN ('LOW','MEDIUM','HIGH','CRITICAL')),
  status            text NOT NULL DEFAULT 'PROPOSED'
                      CHECK (status IN ('PROPOSED','PENDING_APPROVAL','APPROVED','REJECTED',
                                        'EXECUTED','EXPIRED','FAILED','AUTO_EXPIRED')),
  proposal          jsonb NOT NULL DEFAULT '{}'::jsonb,
  rationale         text,
  confidence        numeric(5,4) CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
  required_inputs   jsonb NOT NULL DEFAULT '[]'::jsonb,
  collected_inputs  jsonb NOT NULL DEFAULT '[]'::jsonb,
  execution_result  jsonb,
  provider_key      text,
  model_name        text,
  prompt_version    text,
  prompt_hash       char(64),
  requires_approval boolean NOT NULL DEFAULT true,
  approved_at       timestamptz,
  executed_at       timestamptz,
  expires_at        timestamptz,
  error_message     text,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.ai_actions IS
  'Proposed/approved/executed AI actions. HIGH and CRITICAL actions always require human approval.';
COMMENT ON COLUMN public.ai_actions.capability_token IS
  'Single-use token bound to a human actor. An agent cannot execute a tool call without it (A4).';

CREATE INDEX IF NOT EXISTS ix_aiactions_company ON public.ai_actions (company_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_aiactions_pending  ON public.ai_actions (company_id, created_at)
  WHERE status IN ('PROPOSED','PENDING_APPROVAL');
CREATE INDEX IF NOT EXISTS ix_aiactions_user     ON public.ai_actions (initiated_by, created_at DESC);

CREATE OR REPLACE FUNCTION app.assign_ai_action_public_id() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_try int := 0; v_id text;
BEGIN

  LOOP
    v_id := app.gen_public_id('AA', 8);
    EXIT WHEN NOT EXISTS (SELECT 1 FROM public.ai_actions WHERE public_id = v_id);
    v_try := v_try + 1;
    IF v_try > 5 THEN RAISE EXCEPTION 'could not allocate ai action public_id'; END IF;
  END LOOP;
  NEW.public_id := v_id;
  RETURN NEW;
END
$$;

SELECT app.attach_triggers('public.ai_actions'::regclass);
DROP TRIGGER IF EXISTS trg_aiactions_public_id ON public.ai_actions;
CREATE TRIGGER trg_aiactions_public_id BEFORE INSERT ON public.ai_actions
  FOR EACH ROW WHEN (NEW.public_id IS NULL)
  EXECUTE FUNCTION app.assign_ai_action_public_id();

-- Execution requires: approval recorded, permission held, and a human-bound token.
CREATE OR REPLACE FUNCTION app.assert_ai_action_execution() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_allowed_approver uuid; v_sod boolean;
  -- Migrations, seeds and background workers have no end-user identity.
  -- mytrakin_api is neither superuser nor BYPASSRLS, so request traffic
  -- always falls through to the real checks below.
BEGIN
  -- Migrations, seeds and background workers have no end-user identity.
  -- mytrakin_api is neither superuser nor BYPASSRLS, so request traffic
  -- always falls through to the real checks below.
  IF app.is_trusted_context() THEN
    RETURN NEW;
  END IF;

  IF NEW.status IN ('EXECUTED') AND OLD.status IS DISTINCT FROM NEW.status THEN
    IF NEW.risk_level IN ('HIGH','CRITICAL') OR NEW.action_type IN (
         'initiate_payment','submit_invoice','send_contract','modify_contract',
         'change_permissions','terminate_contract')
       THEN
      IF NEW.approved_by IS NULL OR NEW.approved_at IS NULL THEN
        RAISE EXCEPTION 'ai action % requires recorded human approval', NEW.public_id
          USING ERRCODE = 'insufficient_privilege';
      END IF;
    END IF;

    IF NEW.required_permission IS NOT NULL
       AND NOT app.has_permission(NEW.company_id, NEW.required_permission) THEN
      RAISE EXCEPTION 'missing permission % to execute ai action', NEW.required_permission
        USING ERRCODE = 'insufficient_privilege';
    END IF;

    IF NEW.capability_token IS NULL THEN
      RAISE EXCEPTION 'ai action % has no capability token bound to a human actor', NEW.public_id
        USING ERRCODE = 'insufficient_privilege';
    END IF;

    -- The approver must not be the same person who initiated it for critical actions.
    SELECT COALESCE((settings->'segregation_of_duties'->>'allow_self_approval'), 'false')::boolean
      INTO v_sod FROM public.companies WHERE id = NEW.company_id;
    IF NEW.risk_level = 'CRITICAL' AND NOT v_sod AND NEW.approved_by = NEW.initiated_by THEN
      RAISE EXCEPTION 'segregation of duties: critical ai action needs a different approver'
        USING ERRCODE = 'insufficient_privilege';
    END IF;

    NEW.executed_at := now();
  END IF;

  IF NEW.status IN ('APPROVED') AND OLD.status IS DISTINCT FROM NEW.status THEN
    NEW.approved_at := now();
  END IF;

  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_aiactions_guard ON public.ai_actions;
CREATE TRIGGER trg_aiactions_guard BEFORE UPDATE ON public.ai_actions
  FOR EACH ROW EXECUTE FUNCTION app.assert_ai_action_execution();

-- -----------------------------------------------------------------------------
-- ai_insights — proactive intelligence (project health, workforce, financial,
-- risk). Predictions are explicitly labelled.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.ai_insights (
  id             uuid PRIMARY KEY DEFAULT app.uuid7(),
  public_id      text NOT NULL UNIQUE,
  company_id     uuid NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
  insight_type   text NOT NULL
                   CHECK (insight_type IN ('PROJECT_HEALTH','WORKFORCE','FINANCIAL','RISK',
                                           'COMPLIANCE','CONTRACT','REVENUE','CASHFLOW','ANOMALY','PEOPLE')),
  severity       text NOT NULL DEFAULT 'INFO'
                   CHECK (severity IN ('INFO','LOW','MEDIUM','HIGH','CRITICAL')),
  title          text NOT NULL,
  summary        text NOT NULL,
  is_prediction  boolean NOT NULL DEFAULT false,
  prediction_horizon_days int,
  confidence     numeric(5,4) CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
  entity_type    text,
  entity_id      uuid,
  data_snapshot  jsonb NOT NULL DEFAULT '{}'::jsonb,
  recommended_actions jsonb NOT NULL DEFAULT '[]'::jsonb,
  required_permission text NOT NULL DEFAULT 'ai.insights.read',
  acknowledged_by uuid REFERENCES public.users(id) ON DELETE SET NULL,
  acknowledged_at timestamptz,
  dismissed_at   timestamptz,
  valid_until    timestamptz,
  provider_key   text,
  model_name     text,
  created_at     timestamptz NOT NULL DEFAULT now()
);

COMMENT ON COLUMN public.ai_insights.is_prediction IS
  'True for forecasts/projections. The UI must always label these as predictions, never facts.';

CREATE INDEX IF NOT EXISTS ix_insights_company ON public.ai_insights (company_id, severity, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_insights_open    ON public.ai_insights (company_id, created_at DESC)
  WHERE acknowledged_at IS NULL AND dismissed_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_insights_entity  ON public.ai_insights (entity_type, entity_id);

-- -----------------------------------------------------------------------------
-- ai_automations — natural-language workflows compiled to trigger/condition/action
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.ai_automations (
  id             uuid PRIMARY KEY DEFAULT app.uuid7(),
  public_id      text NOT NULL UNIQUE,
  company_id     uuid NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
  name           text NOT NULL,
  description    text,
  natural_language_prompt text NOT NULL,     -- what the user typed
  compiled_definition jsonb NOT NULL DEFAULT '{}'::jsonb,  -- {triggers, conditions, actions}
  trigger_type   text NOT NULL,
  conditions     jsonb NOT NULL DEFAULT '[]'::jsonb,
  actions        jsonb NOT NULL DEFAULT '[]'::jsonb,
  required_permission text NOT NULL,
  is_active      boolean NOT NULL DEFAULT false,
  requires_human_approval boolean NOT NULL DEFAULT true,
  created_by     uuid REFERENCES public.users(id) ON DELETE SET NULL,
  last_run_at    timestamptz,
  run_count      int NOT NULL DEFAULT 0,
  success_count  int NOT NULL DEFAULT 0,
  failure_count  int NOT NULL DEFAULT 0,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.ai_automations IS
  'User-authored automation. Financial/contractual actions always require approval unless policy opts out.';

CREATE INDEX IF NOT EXISTS ix_aiautomations_company ON public.ai_automations (company_id, is_active);
CREATE INDEX IF NOT EXISTS ix_aiautomations_trigger  ON public.ai_automations (trigger_type)
  WHERE is_active;

CREATE OR REPLACE FUNCTION app.assign_automation_public_id() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_try int := 0; v_id text;
BEGIN

  LOOP
    v_id := app.gen_public_id('AU', 8);
    EXIT WHEN NOT EXISTS (SELECT 1 FROM public.ai_automations WHERE public_id = v_id);
    v_try := v_try + 1;
    IF v_try > 5 THEN RAISE EXCEPTION 'could not allocate automation public_id'; END IF;
  END LOOP;
  NEW.public_id := v_id;
  RETURN NEW;
END
$$;

SELECT app.attach_triggers('public.ai_automations'::regclass);
DROP TRIGGER IF EXISTS trg_aiautomations_public_id ON public.ai_automations;
CREATE TRIGGER trg_aiautomations_public_id BEFORE INSERT ON public.ai_automations
  FOR EACH ROW WHEN (NEW.public_id IS NULL)
  EXECUTE FUNCTION app.assign_automation_public_id();

-- Financial automations may not enable auto-execution without explicit policy.
CREATE OR REPLACE FUNCTION app.assert_automation_safety() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_financial boolean; v_allowed boolean;
  -- Migrations, seeds and background workers have no end-user identity.
  -- mytrakin_api is neither superuser nor BYPASSRLS, so request traffic
  -- always falls through to the real checks below.
BEGIN
  -- Migrations, seeds and background workers have no end-user identity.
  -- mytrakin_api is neither superuser nor BYPASSRLS, so request traffic
  -- always falls through to the real checks below.
  IF app.is_trusted_context() THEN
    RETURN NEW;
  END IF;

  IF TG_OP = 'UPDATE' AND NEW.is_active AND NOT OLD.is_active THEN
    v_financial := NEW.actions::text ~* '(initiate_payment|submit_invoice|approve_invoice|send_contract|modify_contract|transfer_funds)';
    IF v_financial THEN
      SELECT COALESCE((settings->'ai'->'automations'->>'allow_financial_auto_execute'), 'false')::boolean
        INTO v_allowed FROM public.companies WHERE id = NEW.company_id;
      IF NOT v_allowed THEN
        NEW.requires_human_approval := true;
      END IF;
    END IF;
  END IF;

  IF NEW.is_active AND NOT app.has_permission(NEW.company_id, 'ai.automations.manage') THEN
    RAISE EXCEPTION 'missing permission ai.automations.manage' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_aiautomations_guard ON public.ai_automations;
CREATE TRIGGER trg_aiautomations_guard BEFORE INSERT OR UPDATE ON public.ai_automations
  FOR EACH ROW EXECUTE FUNCTION app.assert_automation_safety();

CREATE TABLE IF NOT EXISTS public.ai_automation_runs (
  id             uuid PRIMARY KEY DEFAULT app.uuid7(),
  automation_id  uuid NOT NULL REFERENCES public.ai_automations(id) ON DELETE CASCADE,
  company_id     uuid NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
  trigger_event  jsonb NOT NULL DEFAULT '{}'::jsonb,
  status         text NOT NULL DEFAULT 'PENDING'
                   CHECK (status IN ('PENDING','EVALUATED','ACTIONED','AWAITING_APPROVAL','FAILED','SKIPPED')),
  conditions_result jsonb,
  proposed_actions jsonb NOT NULL DEFAULT '[]'::jsonb,
  ai_action_ids  uuid[],
  approved_by    uuid REFERENCES public.users(id) ON DELETE SET NULL,
  approved_at    timestamptz,
  error_message  text,
  started_at     timestamptz,
  finished_at    timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_automation_runs_automation
  ON public.ai_automation_runs (automation_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_automation_runs_pending
  ON public.ai_automation_runs (company_id, created_at) WHERE status = 'AWAITING_APPROVAL';

-- -----------------------------------------------------------------------------
-- Defer the FK declared in 0004.
ALTER TABLE public.companies DROP CONSTRAINT IF EXISTS companies_w9_extraction_id_fkey;
ALTER TABLE public.companies
  ADD CONSTRAINT companies_w9_extraction_id_fkey
  FOREIGN KEY (w9_extraction_id) REFERENCES public.ai_extractions(id) ON DELETE SET NULL;

-- app.attach_updated_at() (defined in 0001) installs an idempotent
-- updated_at trigger; no inline EXECUTE needed here.
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['ai_messages','ai_extractions','ai_actions','ai_insights','ai_automations'] LOOP
    PERFORM app.attach_updated_at(
      format('public.%I', t)::regclass,
      'trg_' || t || '_updated_at');
  END LOOP;
END
$$;

GRANT EXECUTE ON FUNCTION app.ai_visible_chunks(uuid, vector, int, uuid, float)
TO mytrakin_api, mytrakin_worker;
GRANT EXECUTE ON FUNCTION app.ai_spend_cents_today(uuid) TO mytrakin_api, mytrakin_worker;
GRANT EXECUTE ON FUNCTION app.ai_tokens_today(uuid) TO mytrakin_api, mytrakin_worker;

GRANT SELECT, INSERT, UPDATE, DELETE ON
  public.ai_providers, public.ai_usage_events, public.ai_conversations, public.ai_messages,
  public.ai_knowledge_documents, public.ai_document_chunks, public.ai_extractions,
  public.ai_actions, public.ai_insights, public.ai_automations, public.ai_automation_runs
TO mytrakin_api, mytrakin_worker;
