-- =============================================================================
-- MyTrakin :: 0002_platform.sql
-- Cross-cutting infrastructure: audit ledger, transactional outbox, idempotency,
-- feature flags, notifications, tasks, rate-limit counters, connection metadata.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Audit ledger. Append-only; every sensitive operation writes here inside the
-- same transaction as the mutation it describes.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS platform.audit_logs (
  id                uuid PRIMARY KEY DEFAULT app.uuid7(),
  occurred_at       timestamptz NOT NULL DEFAULT now(),
  company_id        uuid,                       -- NULL for platform-scope events
  actor_user_id     uuid,                       -- NULL for system/agent actors
  actor_type        public.actor_type NOT NULL DEFAULT 'USER',
  actor_label       text,                       -- system job name / agent id
  action            text NOT NULL,              -- e.g. contract.approved
  resource_type     text NOT NULL,              -- e.g. contract
  resource_id       uuid,
  resource_public_id text,                      -- human-readable, for support lookups
  old_values        jsonb,
  new_values        jsonb,
  changed_fields    text[],
  reason            text,
  request_id        text,
  ip_address        inet,
  user_agent        text,
  metadata          jsonb NOT NULL DEFAULT '{}'::jsonb
);

COMMENT ON TABLE platform.audit_logs IS
  'Immutable audit ledger. Append-only; sensitive values are redacted before insert.';
COMMENT ON COLUMN platform.audit_logs.metadata IS
  'Non-sensitive context only. Never store secrets, tokens, full TIN/SSN or bank credentials here.';

CREATE INDEX IF NOT EXISTS ix_audit_company_time
  ON platform.audit_logs (company_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS ix_audit_actor_time
  ON platform.audit_logs (actor_user_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS ix_audit_resource
  ON platform.audit_logs (resource_type, resource_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS ix_audit_action_time
  ON platform.audit_logs (action, occurred_at DESC);
CREATE INDEX IF NOT EXISTS ix_audit_request
  ON platform.audit_logs (request_id) WHERE request_id IS NOT NULL;

-- Prevent UPDATE and DELETE at the database level, even for privileged roles.
-- Append-only for EVERY role, with no trusted-context escape. If an operator or a
-- compromised superuser session could rewrite the audit trail, the ledger would
-- provide no assurance at all. Retention is handled by partitioning/archival, not
-- by permitting mutation.
CREATE OR REPLACE FUNCTION platform.audit_logs_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'audit_logs is append-only; % rejected', TG_OP
    USING ERRCODE = 'restrict_violation';
END
$$;

DROP TRIGGER IF EXISTS trg_audit_immutable ON platform.audit_logs;
CREATE TRIGGER trg_audit_immutable
  BEFORE UPDATE OR DELETE ON platform.audit_logs
  FOR EACH ROW EXECUTE FUNCTION platform.audit_logs_immutable();

-- -----------------------------------------------------------------------------
-- Transactional outbox. Domain writes and their events commit atomically; the
-- dispatcher moves rows to Celery/Redis. Guarantees at-least-once delivery with
-- consumer-side idempotency.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS platform.outbox_events (
  id             uuid PRIMARY KEY DEFAULT app.uuid7(),
  created_at     timestamptz NOT NULL DEFAULT now(),
  event_type     text NOT NULL,
  event_version  int NOT NULL DEFAULT 1,
  company_id     uuid,
  actor_user_id  uuid,
  aggregate_type text NOT NULL,
  aggregate_id   uuid NOT NULL,
  payload        jsonb NOT NULL DEFAULT '{}'::jsonb,
  idempotency_key text,
  published_at   timestamptz,
  attempts       int NOT NULL DEFAULT 0,
  last_error     text,
  next_attempt_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_outbox_unpublished
  ON platform.outbox_events (next_attempt_at, created_at)
  WHERE published_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_outbox_aggregate
  ON platform.outbox_events (aggregate_type, aggregate_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS ux_outbox_idempotency
  ON platform.outbox_events (event_type, idempotency_key)
  WHERE idempotency_key IS NOT NULL;

COMMENT ON TABLE platform.outbox_events IS
  'Transactional outbox. Written in the same transaction as domain state; dispatched at-least-once.';

-- -----------------------------------------------------------------------------
-- Idempotency keys for financial and externally-visible operations.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS platform.idempotency_keys (
  key             text PRIMARY KEY,
  scope           text NOT NULL,             -- e.g. invoice.generate
  company_id      uuid,
  user_id         uuid,
  request_hash    text NOT NULL,             -- sha256 of canonical request body
  response_status int,
  response_body   jsonb,
  locked_at       timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  expires_at      timestamptz NOT NULL DEFAULT (now() + interval '7 days')
);

CREATE INDEX IF NOT EXISTS ix_idem_company ON platform.idempotency_keys (company_id, scope, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_idem_expiry   ON platform.idempotency_keys (expires_at);

COMMENT ON TABLE platform.idempotency_keys IS
  'Replay protection. Reuse with a different request hash is rejected as IDEMPOTENCY_KEY_REUSED.';

-- -----------------------------------------------------------------------------
-- Feature flags: platform default, optional company or user override.
-- Resolution order: user override > company override > platform default.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS platform.feature_flags (
  key          text PRIMARY KEY,
  description  text NOT NULL DEFAULT '',
  enabled      boolean NOT NULL DEFAULT false,
  rollout_pct  int NOT NULL DEFAULT 0 CHECK (rollout_pct BETWEEN 0 AND 100),
  config       jsonb NOT NULL DEFAULT '{}'::jsonb,
  updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS platform.feature_flag_overrides (
  id          uuid PRIMARY KEY DEFAULT app.uuid7(),
  flag_key    text NOT NULL REFERENCES platform.feature_flags(key) ON DELETE CASCADE,
  scope_type  text NOT NULL CHECK (scope_type IN ('COMPANY', 'USER')),
  scope_id    uuid NOT NULL,
  enabled     boolean NOT NULL,
  config      jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (flag_key, scope_type, scope_id)
);

CREATE INDEX IF NOT EXISTS ix_flag_override_scope
  ON platform.feature_flag_overrides (scope_type, scope_id);

COMMENT ON TABLE platform.feature_flag_overrides IS
  'Per-company and per-user overrides for gradual rollout and kill switches.';

-- -----------------------------------------------------------------------------
-- Notifications
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS platform.notifications (
  id           uuid PRIMARY KEY DEFAULT app.uuid7(),
  user_id      uuid NOT NULL,
  company_id   uuid,
  type         text NOT NULL,                 -- contract.timesheet_approved, ai.risk_alert, ...
  title        text NOT NULL,
  body         text,
  resource_type text,
  resource_id  uuid,
  resource_public_id text,
  action_url   text,
  severity     text NOT NULL DEFAULT 'INFO'
                 CHECK (severity IN ('INFO', 'SUCCESS', 'WARNING', 'CRITICAL')),
  read_at      timestamptz,
  email_sent_at timestamptz,
  metadata     jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_notifications_user_unread
  ON platform.notifications (user_id, created_at DESC) WHERE read_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_notifications_user_time
  ON platform.notifications (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_notifications_type
  ON platform.notifications (type, created_at DESC);

-- -----------------------------------------------------------------------------
-- Notification delivery preferences (in-app always on; email/push opt-in)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS platform.notification_preferences (
  user_id       uuid NOT NULL,
  category      text NOT NULL,                -- CONTRACT, TIMESHEET, INVOICE, ...
  in_app        boolean NOT NULL DEFAULT true,
  email         boolean NOT NULL DEFAULT true,
  push          boolean NOT NULL DEFAULT true,
  digest        text NOT NULL DEFAULT 'IMMEDIATE'
                  CHECK (digest IN ('IMMEDIATE', 'DAILY', 'WEEKLY', 'OFF')),
  quiet_hours_start time,
  quiet_hours_end   text,
  updated_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, category)
);

-- -----------------------------------------------------------------------------
-- Background jobs: durable task state, retries and dead-letter handling
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS platform.tasks (
  id             uuid PRIMARY KEY DEFAULT app.uuid7(),
  task_id        text UNIQUE,
  kind           text NOT NULL,
  status         text NOT NULL DEFAULT 'PENDING'
                   CHECK (status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'RETRYING', 'DEAD')),
  company_id     uuid,
  requested_by   uuid,
  payload        jsonb NOT NULL DEFAULT '{}'::jsonb,
  result         jsonb,
  error_message  text,
  attempts       int NOT NULL DEFAULT 0,
  max_attempts   int NOT NULL DEFAULT 5,
  idempotency_key text,
  started_at     timestamptz,
  finished_at    timestamptz,
  next_attempt_at timestamptz NOT NULL DEFAULT now(),
  created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_tasks_pending  ON platform.tasks (next_attempt_at) WHERE status IN ('PENDING','RETRYING');
CREATE INDEX IF NOT EXISTS ix_tasks_dead     ON platform.tasks (created_at DESC) WHERE status = 'DEAD';
CREATE UNIQUE INDEX IF NOT EXISTS ux_tasks_idem ON platform.tasks (kind, idempotency_key)
  WHERE idempotency_key IS NOT NULL;

-- -----------------------------------------------------------------------------
-- Rate limiting counters (Redis is the hot path; this is the durable record and
-- supports per-user fairness reporting).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS platform.rate_limit_counters (
  bucket         text NOT NULL,
  subject        text NOT NULL,
  window_started_at timestamptz NOT NULL,
  count          int NOT NULL DEFAULT 0,
  PRIMARY KEY (bucket, subject, window_started_at)
);

CREATE INDEX IF NOT EXISTS ix_ratelimit_window ON platform.rate_limit_counters (window_started_at DESC);

-- -----------------------------------------------------------------------------
-- User sessions (mirrors Supabase Auth for revocation + device visibility)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS platform.user_sessions (
  id                uuid PRIMARY KEY DEFAULT app.uuid7(),
  user_id           uuid NOT NULL,
  supabase_session_id text,
  ip_address        inet,
  user_agent        text,
  device_label      text,
  created_at        timestamptz NOT NULL DEFAULT now(),
  last_seen_at      timestamptz NOT NULL DEFAULT now(),
  revoked_at        timestamptz,
  revoked_reason    text
);

CREATE INDEX IF NOT EXISTS ix_sessions_user ON platform.user_sessions (user_id, last_seen_at DESC);
CREATE INDEX IF NOT EXISTS ix_sessions_active
  ON platform.user_sessions (user_id) WHERE revoked_at IS NULL;

-- -----------------------------------------------------------------------------
-- Grant access to the application roles
-- -----------------------------------------------------------------------------
GRANT USAGE ON SCHEMA platform TO mytrakin_api, mytrakin_worker, mytrakin_readonly;
GRANT SELECT, INSERT ON platform.audit_logs TO mytrakin_api, mytrakin_worker;
GRANT SELECT, INSERT, UPDATE ON platform.outbox_events TO mytrakin_api, mytrakin_worker;
GRANT SELECT, INSERT, UPDATE ON platform.idempotency_keys TO mytrakin_api, mytrakin_worker;
GRANT SELECT ON platform.feature_flags, platform.feature_flag_overrides TO mytrakin_api, mytrakin_readonly;
GRANT SELECT, INSERT, UPDATE, DELETE ON platform.feature_flag_overrides TO mytrakin_worker;
GRANT SELECT, INSERT, UPDATE ON platform.notifications TO mytrakin_api, mytrakin_worker;
GRANT SELECT, INSERT, UPDATE ON platform.notification_preferences TO mytrakin_api;
GRANT SELECT, INSERT, UPDATE ON platform.tasks TO mytrakin_api, mytrakin_worker;
GRANT SELECT, INSERT, UPDATE ON platform.user_sessions TO mytrakin_api, mytrakin_worker;
