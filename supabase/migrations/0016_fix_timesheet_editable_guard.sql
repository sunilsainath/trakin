-- =============================================================================
-- MyTrakin :: 0016_fix_timesheet_editable_guard.sql
--
-- Repairs one function shipped by 0015. Nothing else changes: no table, column,
-- index or grant is added, because the schema already expresses every rule this
-- migration is about.
--
-- Fixed
--   1. app.assert_timesheet_editable() read `ts.locked` from public.timesheets.
--      That table has `locked_at timestamptz` (0008) and no boolean `locked`
--      column -- `locked` exists on contracts (0007) and invoices (0009), which
--      is where the mistake came from. Because trg_entries_timesheet_editable
--      fires BEFORE INSERT OR UPDATE OR DELETE on public.timesheet_entries, the
--      undefined column made *every* entry write fail with UndefinedColumn,
--      including writes to a DRAFT sheet that the rule was never meant to touch,
--      and the intended `timesheet_locked` check_violation was unreachable.
--      The guard now reads `locked_at IS NOT NULL`, matching app.compute_entry()
--      (0008 W2), which already used the correct column.
--
-- The historical migration files are forward-only and stay untouched; the ledger
-- in platform.schema_migrations records this file as a separate version.
-- =============================================================================

CREATE OR REPLACE FUNCTION app.assert_timesheet_editable() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_status public.timesheet_status; v_locked boolean; v_context text;
BEGIN
  -- locked_at is a timestamptz. The lock is a fact about a moment, so "is it
  -- locked" is `locked_at IS NOT NULL`, never a boolean column.
  SELECT ts.status, (ts.locked_at IS NOT NULL) INTO v_status, v_locked
    FROM public.timesheets ts WHERE ts.id = COALESCE(NEW.timesheet_id, OLD.timesheet_id);

  IF v_status IS NULL THEN RETURN NULL; END IF;   -- parent gone; CASCADE handles it

  IF TG_OP = 'DELETE' THEN
    IF v_status IN ('APPROVED', 'LOCKED') OR v_locked THEN
      RAISE EXCEPTION 'timesheet_locked'
        USING ERRCODE = 'restrict_violation',
              HINT   = 'An approved or locked timesheet cannot have entries removed.';
    END IF;
    RETURN OLD;
  END IF;

  -- A company policy may require the trusted (migration / worker) context to
  -- amend a locked sheet; ordinary requests never get that path.
  v_context := current_setting('app.actor_type', true);
  IF (v_status IN ('APPROVED', 'LOCKED') OR v_locked) AND v_context IS DISTINCT FROM 'SYSTEM' THEN
    RAISE EXCEPTION 'timesheet_locked'
      USING ERRCODE = 'restrict_violation',
            HINT   = 'An approved or locked timesheet cannot be edited. Raise a revision instead.';
  END IF;
  RETURN NEW;
END $$;

COMMENT ON FUNCTION app.assert_timesheet_editable() IS
  'Rule 3/rule 6: entries on an APPROVED or LOCKED timesheet are refused unless the session is the trusted SYSTEM context. Reads timesheets.locked_at (a timestamptz), not a boolean column.';

-- The trigger definition is unchanged; only the function body it calls needed
-- repair. Re-creating it keeps 0016 self-sufficient on a database that already
-- ran 0015.
DROP TRIGGER IF EXISTS trg_entries_timesheet_editable ON public.timesheet_entries;
CREATE TRIGGER trg_entries_timesheet_editable
  BEFORE INSERT OR UPDATE OR DELETE ON public.timesheet_entries
  FOR EACH ROW EXECUTE FUNCTION app.assert_timesheet_editable();
