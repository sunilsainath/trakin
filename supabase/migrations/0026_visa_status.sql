-- =============================================================================
-- MyTrakin :: 0026_visa_status.sql
--
-- Work authorization belongs with the other protected identifiers on
-- `user_sensitive`: readable by the owner only, never joined into profile
-- responses, never logged. The value is a short status label (e.g.
-- CITIZEN, PERMANENT_RESIDENT, H1B, F1_OPT, OTHER) plus an optional detail;
-- anything finer lives in documents, not in a queryable column.
-- =============================================================================

ALTER TABLE public.user_sensitive
  ADD COLUMN IF NOT EXISTS visa_status text,
  ADD COLUMN IF NOT EXISTS work_authorization text;

COMMENT ON COLUMN public.user_sensitive.visa_status IS
  'Owner-only work-authorization label. Exposed solely through the masked sensitive projection.';
COMMENT ON COLUMN public.user_sensitive.work_authorization IS
  'Owner-only detail (e.g. EAD category). Exposed solely through the masked sensitive projection.';
