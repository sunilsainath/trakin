-- =============================================================================
-- MyTrakin :: 0017_contract_sow_metadata.sql
--
-- Adds the flexible `metadata` store to the two commercial tables whose
-- service layer already reads and writes it.
--
-- Why this is a schema fix, not a code fix
--   * app/services/contracts.py selects/inserts/updates `contracts.metadata`
--     (agreed rate snapshot, contract_value mirror, renewals history).
--   * app/services/code.py selects `sows.metadata` and merges scope /
--     deliverables / milestones updates into it.
--   * The sibling tables (projects, payments, msas, line items) all carry an
--     identical `metadata JSONB NOT NULL DEFAULT '{}'` column, so this aligns
--     contracts/sows with the codebase-wide convention rather than inventing
--     a new pattern.
--
-- Backfill: existing rows get '{}', which the readers already treat as "no
-- scope / no renewals" via json_or_empty().
--
-- The historical migration files are forward-only and stay untouched; the ledger
-- in platform.schema_migrations records this file as a separate version.
-- =============================================================================

ALTER TABLE public.contracts
  ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE public.sows
  ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN public.contracts.metadata IS
  'Agreed commercial snapshot (contract_value mirror, renewals history). Read by contracts.get_contract; never a substitute for typed columns.';
COMMENT ON COLUMN public.sows.metadata IS
  'Commercial scope details (scope, deliverables, milestones). Read by code.get_sow; merged, never replaced, on update.';
