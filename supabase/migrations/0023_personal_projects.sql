-- =============================================================================
-- MyTrakin :: 0023_personal_projects.sql
--
-- CODE §5: any registered user can create an INDIVIDUAL project without any
-- company membership. Company projects keep the existing membership +
-- permission path untouched.
--
-- Shape: projects.company_id and project_roles.company_id become nullable.
-- A NULL company means "personal": owned by projects.owner_user_id (roles
-- inherit ownership through their project, so no new columns there).
--
-- RLS: each policy gains an owner branch. Company rows evaluate exactly as
-- before; personal rows are visible/writable only by their owner. CROSS-tenant
-- isolation is preserved: a NULL company never matches another tenant, and
-- owner checks use the session identity, never a client-supplied id.
-- =============================================================================

ALTER TABLE public.projects ALTER COLUMN company_id DROP NOT NULL;
ALTER TABLE public.project_roles ALTER COLUMN company_id DROP NOT NULL;

DROP POLICY IF EXISTS projects_read ON public.projects;
CREATE POLICY projects_read ON public.projects
  FOR SELECT TO public
  USING (
    deleted_at IS NULL AND (
      app.row_company_read(company_id, 'projects.read')
      OR (company_id IS NULL AND owner_user_id = app.current_user_id())
    )
  );

DROP POLICY IF EXISTS projects_write ON public.projects;
CREATE POLICY projects_write ON public.projects
  FOR ALL TO public
  USING (
    app.row_company_read(company_id, 'projects.update')
    OR (company_id IS NULL AND owner_user_id = app.current_user_id())
  )
  WITH CHECK (
    app.row_company_read(company_id, 'projects.create')
    OR (company_id IS NULL AND owner_user_id = app.current_user_id())
  );

DROP POLICY IF EXISTS project_roles_read ON public.project_roles;
CREATE POLICY project_roles_read ON public.project_roles
  FOR SELECT TO public
  USING (
    deleted_at IS NULL AND (
      app.row_company_read(company_id, 'projects.read')
      OR (
        company_id IS NULL AND EXISTS (
          SELECT 1 FROM public.projects p
           WHERE p.id = project_id AND p.owner_user_id = app.current_user_id()
        )
      )
    )
  );

DROP POLICY IF EXISTS project_roles_write ON public.project_roles;
CREATE POLICY project_roles_write ON public.project_roles
  FOR ALL TO public
  USING (
    app.row_company_read(company_id, 'projects.update')
    OR (
      company_id IS NULL AND EXISTS (
        SELECT 1 FROM public.projects p
         WHERE p.id = project_id AND p.owner_user_id = app.current_user_id()
      )
    )
  )
  WITH CHECK (
    app.row_company_read(company_id, 'projects.create')
    OR (
      company_id IS NULL AND EXISTS (
        SELECT 1 FROM public.projects p
         WHERE p.id = project_id AND p.owner_user_id = app.current_user_id()
      )
    )
  );
