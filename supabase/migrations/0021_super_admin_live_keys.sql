-- =============================================================================
-- MyTrakin :: 0021_super_admin_live_keys.sql
--
-- The SUPER_ADMIN template was seeded as ARRAY(SELECT key FROM permissions),
-- which snapshots the catalogue at seed time: keys added later (e.g. 0018's
-- posts.react/posts.comment) never reach it, and the full-set permission test
-- fails 118 vs 120.
--
-- Fix at the source instead of patching rows: bootstrap now grants SUPER_ADMIN
-- the live catalogue on every call, and existing SUPER_ADMIN roles are
-- backfilled. Template arrays for the other roles stay curated by hand.
-- =============================================================================

CREATE OR REPLACE FUNCTION app.bootstrap_company_roles(
  p_company_id uuid,
  p_founder_user_id uuid
) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
  r record;
  v_role_id uuid;
BEGIN

  FOR r IN SELECT * FROM public.role_templates ORDER BY sort_order
  LOOP
    INSERT INTO public.company_roles (company_id, key, name, description, is_system, is_assignable, created_by)
    VALUES (p_company_id, r.key, r.name, r.description, true, r.is_assignable, p_founder_user_id)
    ON CONFLICT (company_id, key) DO UPDATE SET name = EXCLUDED.name
    RETURNING id INTO v_role_id;

    INSERT INTO public.role_permissions (role_id, permission_key, granted_by)
    SELECT v_role_id, k, p_founder_user_id
      FROM unnest(r.permission_keys) AS k
      JOIN public.permissions p ON p.key = k
    ON CONFLICT DO NOTHING;
  END LOOP;

  -- SUPER_ADMIN is the full live catalogue, not the seeded snapshot.
  SELECT id INTO v_role_id
    FROM public.company_roles WHERE company_id = p_company_id AND key = 'SUPER_ADMIN';

  INSERT INTO public.role_permissions (role_id, permission_key, granted_by)
  SELECT v_role_id, p.key, p_founder_user_id
    FROM public.permissions p
  ON CONFLICT DO NOTHING;

  INSERT INTO public.company_memberships
    (company_id, user_id, role_id, status, joined_at)
  VALUES (p_company_id, p_founder_user_id, v_role_id, 'ACTIVE', now())
  ON CONFLICT (company_id, user_id) DO UPDATE
    SET role_id = v_role_id, status = 'ACTIVE', joined_at = now();
END
$$;

-- Backfill companies bootstrapped before this fix.
INSERT INTO public.role_permissions (role_id, permission_key, granted_by)
SELECT cr.id, p.key, cr.created_by
  FROM public.company_roles cr
  CROSS JOIN public.permissions p
 WHERE cr.key = 'SUPER_ADMIN'
ON CONFLICT DO NOTHING;
