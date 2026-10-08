-- =============================================================================
-- MyTrakin :: 0025_personal_sow_roles.sql
--
-- CODE Phase 2: role bindings under a personal (company-less) SOW must be
-- readable/writable by the SOW's owner. Company paths are untouched.
-- =============================================================================

DROP POLICY IF EXISTS sow_roles_read ON public.sow_roles;
CREATE POLICY sow_roles_read ON public.sow_roles
  FOR SELECT TO public
  USING (
    app.can_view_sow(sow_id)
    OR EXISTS (
      SELECT 1 FROM public.sows s
       WHERE s.id = sow_roles.sow_id
         AND s.company_id IS NULL AND s.created_by = app.current_user_id()
    )
  );

DROP POLICY IF EXISTS sow_roles_write ON public.sow_roles;
CREATE POLICY sow_roles_write ON public.sow_roles
  FOR ALL TO public
  USING (
    EXISTS (
      SELECT 1 FROM public.sows s
       WHERE s.id = sow_roles.sow_id
         AND app.row_company_read(s.company_id, 'sows.update')
    )
    OR EXISTS (
      SELECT 1 FROM public.sows s
       WHERE s.id = sow_roles.sow_id
         AND s.company_id IS NULL AND s.created_by = app.current_user_id()
    )
  )
  WITH CHECK (
    EXISTS (
      SELECT 1 FROM public.sows s
       WHERE s.id = sow_roles.sow_id
         AND app.row_company_read(s.company_id, 'sows.update')
    )
    OR EXISTS (
      SELECT 1 FROM public.sows s
       WHERE s.id = sow_roles.sow_id
         AND s.company_id IS NULL AND s.created_by = app.current_user_id()
    )
  );
