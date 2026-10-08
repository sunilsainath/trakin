-- =============================================================================
-- MyTrakin :: 0020_schedule_public_id.sql
--
-- public.payment_schedules is the only tenant table with a NOT NULL public_id
-- and no allocator: every INSERT (the API service and the
-- advance_payment_schedules worker alike) fails with NotNullViolation.
--
-- Adds the standard pair every sibling table has: an assign trigger
-- (PS + 8 base32 chars via app.gen_public_id) and the immutability guard.
-- There is no format CHECK on this table, so no constraint change is needed.
-- =============================================================================

CREATE OR REPLACE FUNCTION app.assign_schedule_public_id()
RETURNS trigger
LANGUAGE plpgsql AS $function$
DECLARE v_try int := 0; v_id text;
BEGIN
  LOOP
    v_id := app.gen_public_id('PS', 8);
    EXIT WHEN NOT EXISTS (SELECT 1 FROM public.payment_schedules WHERE public_id = v_id);
    v_try := v_try + 1;
    IF v_try > 5 THEN RAISE EXCEPTION 'could not allocate schedule public_id'; END IF;
  END LOOP;
  NEW.public_id := v_id;
  RETURN NEW;
END
$function$;

DROP TRIGGER IF EXISTS trg_payment_schedules_public_id ON public.payment_schedules;
CREATE TRIGGER trg_payment_schedules_public_id
  BEFORE INSERT ON public.payment_schedules
  FOR EACH ROW WHEN (new.public_id IS NULL)
  EXECUTE FUNCTION app.assign_schedule_public_id();

DROP TRIGGER IF EXISTS trg_payment_schedules_freeze_public_id ON public.payment_schedules;
CREATE TRIGGER trg_payment_schedules_freeze_public_id
  BEFORE UPDATE ON public.payment_schedules
  FOR EACH ROW
  EXECUTE FUNCTION app.freeze_public_id();
