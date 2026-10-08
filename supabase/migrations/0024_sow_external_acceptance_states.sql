-- =============================================================================
-- MyTrakin :: 0024_sow_external_acceptance_states.sql
--
-- Extends the native `sow_status` enum with the external-acceptance states
-- the service state machine (§14-15) needs: SENT (transmitted to the
-- counterparty), PENDING_ACCEPTANCE (under counterparty review) and REJECTED
-- (declined with a recorded reason; the SOW survives and can be revised).
--
-- NOTE: ALTER TYPE ... ADD VALUE cannot run inside a transaction block. The
-- migration runner connects with autocommit, so each statement commits on
-- its own; do not wrap this file in BEGIN/COMMIT.
-- =============================================================================

ALTER TYPE public.sow_status ADD VALUE IF NOT EXISTS 'SENT';
ALTER TYPE public.sow_status ADD VALUE IF NOT EXISTS 'PENDING_ACCEPTANCE';
ALTER TYPE public.sow_status ADD VALUE IF NOT EXISTS 'REJECTED';

COMMENT ON TYPE public.sow_status IS
  'SOW lifecycle (§14): internal DRAFT -> PENDING_APPROVAL -> ACTIVE, or external DRAFT -> SENT -> PENDING_ACCEPTANCE -> ACTIVE, with REJECTED/EXPIRED/TERMINATED/CLOSED as exits. Accepted SOWs generate their contracts.';
