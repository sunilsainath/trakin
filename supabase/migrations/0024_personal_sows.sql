-- =============================================================================
-- MyTrakin :: 0024_personal_sows.sql
--
-- CODE Phase 2: SOWs for individual (company-less) projects.
--
-- Shape: sows.company_id becomes nullable (NULL = personal, owned by
-- created_by) plus rejection capture columns so a declined SOW keeps its
-- rejected-by/at/reason instead of vanishing.
--
-- RLS: the counterparty branches stay untouched; each policy gains an owner
-- branch for personal rows. Company rows evaluate exactly as before.
-- =============================================================================

ALTER TABLE public.sows ALTER COLUMN company_id DROP NOT NULL;

ALTER TABLE public.sows
  ADD COLUMN IF NOT EXISTS rejected_by uuid NULL REFERENCES public.users(id),
  ADD COLUMN IF NOT EXISTS rejected_at timestamptz NULL,
  ADD COLUMN IF NOT EXISTS reject_reason text NULL;

DROP POLICY IF EXISTS sows_read ON public.sows;
CREATE POLICY sows_read ON public.sows
  FOR SELECT TO public
  USING (
    deleted_at IS NULL AND (
      app.row_company_read(company_id, 'sows.read')
      OR (counterparty_company_id IS NOT NULL AND app.is_member(counterparty_company_id))
      OR (counterparty_user_id = app.current_user_id())
      OR (company_id IS NULL AND created_by = app.current_user_id())
    )
  );

DROP POLICY IF EXISTS sows_write ON public.sows;
CREATE POLICY sows_write ON public.sows
  FOR ALL TO public
  USING (
    app.row_company_read(company_id, 'sows.update')
    OR (company_id IS NULL AND created_by = app.current_user_id())
  )
  WITH CHECK (
    app.row_company_read(company_id, 'sows.create')
    OR (company_id IS NULL AND created_by = app.current_user_id())
  );
