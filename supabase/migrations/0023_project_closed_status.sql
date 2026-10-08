-- =============================================================================
-- MyTrakin :: 0023_project_closed_status.sql
--
-- Widens the project status domain with CLOSED, the terminal state the
-- service state machine (§10: DRAFT -> ACTIVE/ON_HOLD -> COMPLETED/CANCELLED
-- -> CLOSED) needs. The original CHECK from 0007 named the constraint
-- `projects_status_check`; it is replaced, not duplicated, so exactly one
-- status domain exists.
--
-- No data migration: no existing row carries a status outside the old set,
-- and CLOSED is only reachable through the service transition guard.
-- =============================================================================

ALTER TABLE public.projects DROP CONSTRAINT IF EXISTS projects_status_check;
ALTER TABLE public.projects
  ADD CONSTRAINT projects_status_check
  CHECK (status IN ('DRAFT','PLANNING','ACTIVE','ON_HOLD','COMPLETED','CANCELLED','CLOSED'));

COMMENT ON CONSTRAINT projects_status_check ON public.projects IS
  'Project lifecycle (§10). Terminal states COMPLETED/CANCELLED/CLOSED are read-only in the service layer; CLOSED is reachable from any non-DRAFT state as the archival end.';
