-- =============================================================================
-- MyTrakin :: 0025_sow_role_commercial_terms.sql
--
-- Gives each SOW role its own commercial terms, as §12 requires: a role can
-- bill on a different basis (TIMESHEET/FIXED/...) and frequency than the SOW
-- default. Both columns are nullable and fall back to the SOW-level values,
-- so every existing row keeps its meaning: NULL means "same as the SOW".
-- =============================================================================

ALTER TABLE public.sow_roles
  ADD COLUMN IF NOT EXISTS billing_basis public.billing_basis,
  ADD COLUMN IF NOT EXISTS billing_frequency public.billing_frequency;

COMMENT ON COLUMN public.sow_roles.billing_basis IS
  'Per-role billing model (§12). NULL inherits the SOW default.';
COMMENT ON COLUMN public.sow_roles.billing_frequency IS
  'Per-role billing frequency (§12). NULL inherits the SOW default.';
