-- =============================================================================
-- MyTrakin :: 0027_invitation_public_id.sql
--
-- public.company_invitations is the only tenant table with no public_id
-- column, yet both the API service (invite_member RETURNING public_id,
-- accept_invitation/preview_invitation selecting i.public_id) and the web UI
-- (invitation links, Invitation ID display) assume it exists. Every invite
-- attempt therefore fails with UndefinedColumn: the invitation flow never
-- worked end-to-end.
--
-- Adds the standard pair every sibling table has: a nullable-then-NOT NULL
-- public_id (INV + 8 base32 chars via app.gen_public_id, backfilled for the
-- rows that managed to exist), an assign trigger and the immutability guard.
-- =============================================================================

ALTER TABLE public.company_invitations
  ADD COLUMN IF NOT EXISTS public_id text UNIQUE;

DO $$
DECLARE
  r     record;
  v_id  text;
  v_try int;
BEGIN
  FOR r IN SELECT id FROM public.company_invitations WHERE public_id IS NULL LOOP
    v_try := 0;
    LOOP
      v_id := app.gen_public_id('INV', 8);
      EXIT WHEN NOT EXISTS (SELECT 1 FROM public.company_invitations WHERE public_id = v_id);
      v_try := v_try + 1;
      IF v_try > 25 THEN RAISE EXCEPTION 'could not allocate invitation public_id'; END IF;
    END LOOP;
    UPDATE public.company_invitations SET public_id = v_id WHERE id = r.id;
  END LOOP;
END
$$;

ALTER TABLE public.company_invitations ALTER COLUMN public_id SET NOT NULL;

CREATE OR REPLACE FUNCTION app.assign_invitation_public_id()
RETURNS trigger
LANGUAGE plpgsql AS $function$
DECLARE v_try int := 0; v_id text;
BEGIN
  LOOP
    v_id := app.gen_public_id('INV', 8);
    EXIT WHEN NOT EXISTS (SELECT 1 FROM public.company_invitations WHERE public_id = v_id);
    v_try := v_try + 1;
    IF v_try > 5 THEN RAISE EXCEPTION 'could not allocate invitation public_id'; END IF;
  END LOOP;
  NEW.public_id := v_id;
  RETURN NEW;
END
$function$;

DROP TRIGGER IF EXISTS trg_company_invitations_public_id ON public.company_invitations;
CREATE TRIGGER trg_company_invitations_public_id
  BEFORE INSERT ON public.company_invitations
  FOR EACH ROW WHEN (NEW.public_id IS NULL)
  EXECUTE FUNCTION app.assign_invitation_public_id();

DROP TRIGGER IF EXISTS trg_company_invitations_freeze_public_id ON public.company_invitations;
CREATE TRIGGER trg_company_invitations_freeze_public_id
  BEFORE UPDATE ON public.company_invitations
  FOR EACH ROW
  EXECUTE FUNCTION app.freeze_public_id();
