-- =============================================================================
-- MyTrakin :: 0019_fix_mutual_count_recursion.sql
--
-- `trg_connection_mutual` (AFTER INSERT OR DELETE OR UPDATE on
-- public.connections) calls app.refresh_mutual_count(), which itself runs
-- UPDATE public.connections — refiring the same trigger until the backend
-- dies with "stack depth limit exceeded". Every connection accept/remove
-- therefore fails.
--
-- Fix: only write when the value actually changes. A no-op UPDATE touches no
-- rows, fires no row trigger, and the recursion terminates after one level.
-- =============================================================================

CREATE OR REPLACE FUNCTION app.refresh_mutual_count() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public', 'app', 'pg_temp'
AS $function$
DECLARE
  v_self  uuid := COALESCE(NEW.user_low,  OLD.user_low);
  v_other uuid := COALESCE(NEW.user_high, OLD.user_high);
  n int;
BEGIN
  -- Users who are an accepted connection of BOTH endpoints.
  WITH pairs AS (
    SELECT c.user_low AS lo, c.user_high AS hi
      FROM public.connections c
     WHERE c.status = 'ACCEPTED'
       AND (c.user_low = v_self OR c.user_high = v_self)
  ),
  peers AS (SELECT CASE WHEN lo = v_self THEN hi ELSE lo END AS uid FROM pairs),
  peers_of_other AS (
    SELECT CASE WHEN c.user_low = v_other THEN c.user_high ELSE c.user_low END AS uid
      FROM public.connections c
     WHERE c.status = 'ACCEPTED'
       AND (c.user_low = v_other OR c.user_high = v_other)
  )
  SELECT count(*) INTO n FROM (SELECT uid FROM peers INTERSECT SELECT uid FROM peers_of_other) x;

  -- Guarded write: when the count is already correct no row is touched, so
  -- this trigger does not refire itself into infinite recursion.
  UPDATE public.connections SET mutual_count = n
   WHERE user_low = v_self AND user_high = v_other
     AND mutual_count IS DISTINCT FROM n;
  RETURN NULL;
END
$function$;
