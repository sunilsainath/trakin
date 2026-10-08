-- =============================================================================
-- MyTrakin :: 0029_sow_rejected_status.sql
--
-- services/code.py reject_sow() sets status = 'REJECTED', but the sow_status
-- enum never contained that label: every SOW rejection fails with
-- `invalid input value for enum sow_status`. The SOW acceptance flow in the
-- product spec (accept / reject with reason) cannot work without it.
-- =============================================================================

ALTER TYPE public.sow_status ADD VALUE IF NOT EXISTS 'REJECTED';
