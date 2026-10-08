-- =============================================================================
-- MyTrakin :: 0018_social_permissions.sql
--
-- Seeds the two interaction permissions the social API needs and grants them
-- alongside the existing social keys on the member-facing role templates.
--
-- New keys
--   posts.react    react to a visible post
--   posts.comment  comment on a visible post
--
-- SUPER_ADMIN needs no grant: its template is ARRAY(SELECT key FROM
-- public.permissions), so it absorbs new keys automatically. Existing
-- companies keep their current grants (bootstrap copies templates only for
-- new companies); a company admin can grant the new keys via roles.manage.
-- =============================================================================

INSERT INTO public.permissions (key, module, action, description, sensitivity, requires_approval)
VALUES
  ('posts.react',   'social', 'create', 'React to a visible post',        'STANDARD', false),
  ('posts.comment', 'social', 'create', 'Comment on a visible post',      'STANDARD', false)
ON CONFLICT (key) DO NOTHING;

UPDATE public.role_templates
   SET permission_keys = (
         SELECT array_agg(DISTINCT k ORDER BY k)
           FROM unnest(permission_keys || ARRAY['posts.react', 'posts.comment']) AS k
       )
 WHERE key IN ('COMPANY_ADMIN', 'POST_MANAGER', 'EMPLOYEE');
