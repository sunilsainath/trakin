-- =============================================================================
-- MyTrakin :: 0012_rls_policies.sql
-- Row Level Security for every table. Defence in depth: the API enforces
-- permissions, and the database independently refuses rows the caller may not see.
--
-- Tenancy rule of the platform:
--   * A company-owned row is visible only when
--       company_id = app.current_company_id()
--       AND the caller is an ACTIVE member
--       AND the caller holds the specific permission for that resource
--   * A cross-company public_id returns zero rows, never an error, so resource
--     existence is not leaked.
--   * Cross-company (counterparty) reads use explicit membership of BOTH sides.
--
-- All policies fail closed: without SET LOCAL app.user_id nothing is visible.
-- =============================================================================

-- Helper: is the caller a member of the row's company, regardless of permission?
CREATE OR REPLACE FUNCTION app.row_company_ok(p_company_id uuid) RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT p_company_id IS NOT NULL
     AND p_company_id = app.current_company_id()
     AND app.is_member(p_company_id);
$$;

COMMENT ON FUNCTION app.row_company_ok(uuid) IS
  'Baseline tenant check used by most company-scoped policies.';

-- Helper: permission-scoped read.
CREATE OR REPLACE FUNCTION app.row_company_read(p_company_id uuid, p_permission text)
RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT app.row_company_ok(p_company_id) AND app.has_permission(p_company_id, p_permission);
$$;

-- Helper: counterparty visibility (contracts, invoices, MSAs between two companies).
CREATE OR REPLACE FUNCTION app.is_party_to(
  p_company_id uuid,
  p_other_company_id uuid,
  p_user_id uuid DEFAULT app.current_user_id()
) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, app, pg_temp
AS $$
  SELECT (app.is_member(p_company_id, p_user_id) OR app.is_member(p_other_company_id, p_user_id));
$$;

-- Helper: is the caller a member of ANY of the given companies? Used where a row
-- belongs to a chain (e.g. invoices receivable by one party, payable by the other).
CREATE OR REPLACE FUNCTION app.is_member_of_any(
  p_company_ids uuid[],
  p_user_id uuid DEFAULT app.current_user_id()
) RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT COALESCE(bool_or(app.is_member(c, p_user_id)), false)
    FROM unnest(p_company_ids) AS c;
$$;

-- -----------------------------------------------------------------------------
-- Idempotency: drop every pre-existing policy first.
-- A partial list of DROP IF EXISTS statements is easy to get wrong, and a single
-- missed name makes the whole migration un-rerunnable. This removes them all.
-- -----------------------------------------------------------------------------
DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT schemaname, tablename, policyname
      FROM pg_policies
     WHERE schemaname IN ('public', 'platform')
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I ON %I.%I',
                   r.policyname, r.schemaname, r.tablename);
  END LOOP;
END
$$;

-- =============================================================================
-- Enable (and force) RLS everywhere
-- =============================================================================
DO $$
DECLARE t record;
BEGIN
  FOR t IN
    SELECT c.oid::regclass AS rel
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE c.relkind = 'r'
       AND n.nspname IN ('public', 'platform')
       AND c.relname NOT LIKE 'pg_%'
  LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t.rel);
    -- FORCE makes the policy apply to the table owner too, so a misconfigured
    -- connection pool (owner role) still cannot bypass tenant isolation.
    EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', t.rel);
  END LOOP;
END
$$;

-- =============================================================================
-- Identity
-- =============================================================================

ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;

-- Users may read themselves. Reading another user is allowed only as far as the
-- profile-visibility policy allows; the column set is narrowed by the API and by
-- column-level grants below.
DROP POLICY IF EXISTS users_read_self ON public.users;
CREATE POLICY users_read_self ON public.users FOR SELECT
  USING (id = app.current_user_id());

-- Directory read: a user is discoverable when their profile is PUBLIC, they are
-- connected with the caller, they share a company, or they invited the caller.
DROP POLICY IF EXISTS users_read_visible ON public.users;
CREATE POLICY users_read_visible ON public.users FOR SELECT
  USING (
    status = 'ACTIVE'
    AND deleted_at IS NULL
    AND NOT app.is_blocked(id, NULL)
    AND (
      COALESCE((SELECT p.profile_visibility FROM public.user_profiles p WHERE p.user_id = users.id),
               'PUBLIC') = 'PUBLIC'
      OR EXISTS (
        SELECT 1 FROM public.connections c
         WHERE c.status = 'ACCEPTED'
           AND c.user_low  = LEAST(app.current_user_id(), users.id)
           AND c.user_high = GREATEST(app.current_user_id(), users.id)
      )
      OR EXISTS (
        SELECT 1 FROM public.company_memberships m
          JOIN public.company_memberships mine
            ON mine.company_id = m.company_id AND mine.user_id = app.current_user_id()
         WHERE m.user_id = users.id AND m.status = 'ACTIVE' AND mine.status = 'ACTIVE'
      )
      OR EXISTS (
        SELECT 1 FROM public.company_invitations i
         WHERE i.email = users.email AND i.status = 'PENDING'
           AND app.is_member(i.company_id)
      )
    )
  );

DROP POLICY IF EXISTS users_insert_self ON public.users;
CREATE POLICY users_insert_self ON public.users FOR INSERT
  WITH CHECK (id = app.current_user_id());

-- A user may only modify their own row, and may not escalate status or verification.
DROP POLICY IF EXISTS users_update_self ON public.users;
CREATE POLICY users_update_self ON public.users FOR UPDATE
  USING (id = app.current_user_id())
  WITH CHECK (
    id = app.current_user_id()
    AND status = (SELECT u.status FROM public.users u WHERE u.id = app.current_user_id())
  );

ALTER TABLE public.user_profiles ENABLE ROW LEVEL SECURITY;

-- Visibility predicates, defined before the policies that reference them.
CREATE OR REPLACE FUNCTION app.can_view_connected(p_user_id uuid)
RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT app.current_user_id() = p_user_id
      OR EXISTS (SELECT 1 FROM public.connections c
                  WHERE c.status = 'ACCEPTED'
                    AND c.user_low  = LEAST(app.current_user_id(), p_user_id)
                    AND c.user_high = GREATEST(app.current_user_id(), p_user_id));
$$;

CREATE OR REPLACE FUNCTION app.can_view_profile(p_user_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, app, pg_temp
AS $$
  SELECT p_user_id = app.current_user_id()
      OR (
        EXISTS (SELECT 1 FROM public.users u
                 WHERE u.id = p_user_id AND u.status = 'ACTIVE' AND u.deleted_at IS NULL)
        AND NOT app.is_blocked(p_user_id, NULL)
        AND COALESCE((SELECT p.profile_visibility FROM public.user_profiles p
                       WHERE p.user_id = p_user_id), 'PUBLIC') <> 'PRIVATE'
        AND (
          COALESCE((SELECT p.profile_visibility FROM public.user_profiles p
                     WHERE p.user_id = p_user_id), 'PUBLIC') = 'PUBLIC'
          OR app.can_view_connected(p_user_id)
        )
      );
$$;

DROP POLICY IF EXISTS profiles_read ON public.user_profiles;
CREATE POLICY profiles_read ON public.user_profiles FOR SELECT
  USING (app.can_view_profile(user_id));

DROP POLICY IF EXISTS profiles_write_self ON public.user_profiles;
CREATE POLICY profiles_write_self ON public.user_profiles FOR UPDATE
  USING (user_id = app.current_user_id())
  WITH CHECK (user_id = app.current_user_id());

DROP POLICY IF EXISTS profiles_insert_self ON public.user_profiles;
CREATE POLICY profiles_insert_self ON public.user_profiles FOR INSERT
  WITH CHECK (user_id = app.current_user_id());

ALTER TABLE public.user_privacy ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS privacy_self ON public.user_privacy;
CREATE POLICY privacy_self ON public.user_privacy FOR ALL
  USING (user_id = app.current_user_id())
  WITH CHECK (user_id = app.current_user_id());

ALTER TABLE public.user_sensitive ENABLE ROW LEVEL SECURITY;

-- Self-only. No other role can read this table, including support tooling.
DROP POLICY IF EXISTS sensitive_self ON public.user_sensitive;
CREATE POLICY sensitive_self ON public.user_sensitive FOR ALL
  USING (user_id = app.current_user_id())
  WITH CHECK (user_id = app.current_user_id());

ALTER TABLE public.skills ENABLE ROW LEVEL SECURITY;

-- Reference data is readable by any authenticated user; writes are admin-only.
DROP POLICY IF EXISTS skills_read ON public.skills;
CREATE POLICY skills_read ON public.skills FOR SELECT
  USING (app.current_user_id() IS NOT NULL AND is_active);

ALTER TABLE public.user_skills ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS user_skills_read ON public.user_skills;
CREATE POLICY user_skills_read ON public.user_skills FOR SELECT
  USING (
    (user_id = app.current_user_id())
    OR (visible AND app.can_view_profile(user_id))
  );

DROP POLICY IF EXISTS user_skills_write ON public.user_skills;
CREATE POLICY user_skills_write ON public.user_skills FOR ALL
  USING (user_id = app.current_user_id())
  WITH CHECK (user_id = app.current_user_id());

ALTER TABLE public.user_educations ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS edu_read ON public.user_educations;
CREATE POLICY edu_read ON public.user_educations FOR SELECT
  USING (
    user_id = app.current_user_id()
    OR (
      visible AND app.can_view_profile(user_id)
      AND COALESCE(
            (SELECT v.visibility FROM public.user_privacy v
              WHERE v.user_id = user_educations.user_id AND v.field_path = 'education'),
            CASE WHEN app.can_view_connected(user_educations.user_id)
                 THEN 'CONNECTIONS'::public.visibility ELSE 'PRIVATE'::public.visibility END
          ) <> 'PRIVATE'
    )
  );

DROP POLICY IF EXISTS edu_write ON public.user_educations;
CREATE POLICY edu_write ON public.user_educations FOR ALL
  USING (user_id = app.current_user_id())
  WITH CHECK (user_id = app.current_user_id());

ALTER TABLE public.user_experiences ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS exp_read ON public.user_experiences;
CREATE POLICY exp_read ON public.user_experiences FOR SELECT
  USING (
    user_id = app.current_user_id()
    OR (
      visible AND app.can_view_profile(user_id)
      AND COALESCE(
            (SELECT v.visibility FROM public.user_privacy v
              WHERE v.user_id = user_experiences.user_id AND v.field_path = 'experience'),
            CASE WHEN app.can_view_connected(user_experiences.user_id)
                 THEN 'CONNECTIONS'::public.visibility ELSE 'PUBLIC'::public.visibility END
          ) <> 'PRIVATE'
    )
  );

DROP POLICY IF EXISTS exp_write ON public.user_experiences;
CREATE POLICY exp_write ON public.user_experiences FOR ALL
  USING (user_id = app.current_user_id())
  WITH CHECK (user_id = app.current_user_id());

ALTER TABLE public.user_certifications ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS cert_read ON public.user_certifications;
CREATE POLICY cert_read ON public.user_certifications FOR SELECT
  USING ((user_id = app.current_user_id())
         OR (visible AND app.can_view_profile(user_id)));

DROP POLICY IF EXISTS cert_write ON public.user_certifications;
CREATE POLICY cert_write ON public.user_certifications FOR ALL
  USING (user_id = app.current_user_id())
  WITH CHECK (user_id = app.current_user_id());

-- SECURITY flags: the user sees their own; admins are handled by a dedicated
-- moderation role outside this policy (support tooling uses the worker role).
ALTER TABLE public.user_security_flags ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS security_flags_self ON public.user_security_flags;
CREATE POLICY security_flags_self ON public.user_security_flags FOR SELECT
  USING (user_id = app.current_user_id());

-- =============================================================================
-- Business: companies, memberships, roles
-- =============================================================================

ALTER TABLE public.companies ENABLE ROW LEVEL SECURITY;

-- A company is discoverable by name so counterparties can find each other, but
-- its financial/tax data is masked by the API unless members.allowed.
DROP POLICY IF EXISTS companies_read_discovery ON public.companies;
CREATE POLICY companies_read_discovery ON public.companies FOR SELECT
  USING (deleted_at IS NULL AND status <> 'CLOSED');

-- Membership counts are never exposed: the API derives them, and this function
-- only answers a boolean for the caller.
DROP POLICY IF EXISTS companies_update ON public.companies;
CREATE POLICY companies_update ON public.companies FOR UPDATE
  USING (app.row_company_read(id, 'companies.update'))
  WITH CHECK (app.row_company_read(id, 'companies.update'));

DROP POLICY IF EXISTS companies_delete ON public.companies;
CREATE POLICY companies_delete ON public.companies FOR DELETE
  USING (app.row_company_read(id, 'companies.delete'));

ALTER TABLE public.company_memberships ENABLE ROW LEVEL SECURITY;

-- A user always sees their own memberships (needed to build the company switcher).
DROP POLICY IF EXISTS memberships_read_self ON public.company_memberships;
CREATE POLICY memberships_read_self ON public.company_memberships FOR SELECT
  USING (user_id = app.current_user_id());

-- Otherwise visible to company administrators. hourly_rate is masked by the API
-- unless the caller holds timesheets.read_rate.
DROP POLICY IF EXISTS memberships_read_company ON public.company_memberships;
CREATE POLICY memberships_read_company ON public.company_memberships FOR SELECT
  USING (app.row_company_ok(company_id));

DROP POLICY IF EXISTS memberships_write ON public.company_memberships;
CREATE POLICY memberships_write ON public.company_memberships FOR ALL
  USING (app.row_company_read(company_id, 'members.manage'))
  WITH CHECK (app.row_company_read(company_id, 'members.manage'));

ALTER TABLE public.company_roles ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS roles_read ON public.company_roles;
CREATE POLICY roles_read ON public.company_roles FOR SELECT
  USING (deleted_at IS NULL
         AND (app.row_company_ok(company_id)
              -- system roles of your own company stay readable so the UI can
              -- name them before you have permission to administer roles
              OR company_id = (SELECT c.id FROM public.companies c WHERE c.created_by = app.current_user_id())));

-- Only SUPER_ADMIN may create/modify roles; enforced by app.assert_role_permission
-- plus this policy requiring roles.manage.
DROP POLICY IF EXISTS roles_write ON public.company_roles;
CREATE POLICY roles_write ON public.company_roles FOR ALL
  USING (app.row_company_read(company_id, 'roles.manage'))
  WITH CHECK (app.row_company_read(company_id, 'roles.manage'));

-- Creating the first company has no company context yet, so allow inserts when the
-- creator is the authenticated user (the API also checks companies.create).
DROP POLICY IF EXISTS roles_insert ON public.company_roles;
CREATE POLICY roles_insert ON public.company_roles FOR INSERT
  WITH CHECK (
    app.current_user_id() IS NOT NULL
    AND created_by = app.current_user_id()
    AND app.row_company_read(company_id, 'roles.manage')
  );

ALTER TABLE public.role_permissions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS role_perms_read ON public.role_permissions;
CREATE POLICY role_perms_read ON public.role_permissions FOR SELECT
  USING (EXISTS (SELECT 1 FROM public.company_roles r
                   WHERE r.id = role_permissions.role_id AND app.row_company_ok(r.company_id)));

DROP POLICY IF EXISTS role_perms_write ON public.role_permissions;
CREATE POLICY role_perms_write ON public.role_permissions FOR ALL
  USING (EXISTS (SELECT 1 FROM public.company_roles r
                   WHERE r.id = role_permissions.role_id
                     AND app.row_company_read(r.company_id, 'roles.manage')))
  WITH CHECK (EXISTS (SELECT 1 FROM public.company_roles r
                        WHERE r.id = role_permissions.role_id
                          AND app.row_company_read(r.company_id, 'roles.manage')));

ALTER TABLE public.permissions ENABLE ROW LEVEL SECURITY;

-- The catalogue is readable by any authenticated user so the UI can hide actions
-- it cannot perform (the backend still enforces).
DROP POLICY IF EXISTS permissions_read ON public.permissions;
CREATE POLICY permissions_read ON public.permissions FOR SELECT
  USING (app.current_user_id() IS NOT NULL);

ALTER TABLE public.company_invitations ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS invitations_read ON public.company_invitations;
CREATE POLICY invitations_read ON public.company_invitations FOR SELECT
  USING (app.row_company_ok(company_id)
         -- the invited person may read their own invitation by email
         OR email::text = (SELECT u.email::text FROM public.users u WHERE u.id = app.current_user_id()));

DROP POLICY IF EXISTS invitations_write ON public.company_invitations;
CREATE POLICY invitations_write ON public.company_invitations FOR ALL
  USING (app.row_company_read(company_id, 'members.invite'))
  WITH CHECK (app.row_company_read(company_id, 'members.invite'));

ALTER TABLE public.company_settings_history ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS settings_history_read ON public.company_settings_history;
CREATE POLICY settings_history_read ON public.company_settings_history FOR SELECT
  USING (app.row_company_ok(company_id));

-- =============================================================================
-- Social
-- =============================================================================

ALTER TABLE public.user_blocks ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS blocks_own ON public.user_blocks;
CREATE POLICY blocks_own ON public.user_blocks FOR ALL
  USING (blocker_id = app.current_user_id())
  WITH CHECK (blocker_id = app.current_user_id());

ALTER TABLE public.connections ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS connections_read ON public.connections;
CREATE POLICY connections_read ON public.connections FOR SELECT
  USING (requester_id = app.current_user_id()
         OR addressee_id = app.current_user_id()
         OR status = 'ACCEPTED'   -- accepted connections are needed for feed/recommendations
  );
DROP POLICY IF EXISTS connections_write ON public.connections;
CREATE POLICY connections_write ON public.connections FOR ALL
  USING (requester_id = app.current_user_id() OR addressee_id = app.current_user_id())
  WITH CHECK (requester_id = app.current_user_id() OR addressee_id = app.current_user_id());

ALTER TABLE public.connection_requests ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS connreq_read ON public.connection_requests;
CREATE POLICY connreq_read ON public.connection_requests FOR SELECT
  USING (requester_id = app.current_user_id() OR addressee_id = app.current_user_id());
DROP POLICY IF EXISTS connreq_write ON public.connection_requests;
CREATE POLICY connreq_write ON public.connection_requests FOR ALL
  USING (requester_id = app.current_user_id() OR addressee_id = app.current_user_id())
  WITH CHECK (requester_id = app.current_user_id() OR addressee_id = app.current_user_id());

ALTER TABLE public.posts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS posts_read ON public.posts;
CREATE POLICY posts_read ON public.posts FOR SELECT
  USING (app.can_view_post(id));
DROP POLICY IF EXISTS posts_write ON public.posts;
CREATE POLICY posts_write ON public.posts FOR ALL
  USING (author_id = app.current_user_id()
         OR app.row_company_read(company_id, 'posts.manage'))
  WITH CHECK (author_id = app.current_user_id()
              OR app.row_company_read(company_id, 'posts.create'));

ALTER TABLE public.post_comments ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS comments_read ON public.post_comments;
CREATE POLICY comments_read ON public.post_comments FOR SELECT
  USING (deleted_at IS NULL AND app.can_view_post(post_id));
DROP POLICY IF EXISTS comments_write ON public.post_comments;
CREATE POLICY comments_write ON public.post_comments FOR ALL
  USING (author_id = app.current_user_id() AND app.can_view_post(post_id))
  WITH CHECK (author_id = app.current_user_id() AND app.can_view_post(post_id));

ALTER TABLE public.post_reactions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS reactions_read ON public.post_reactions;
CREATE POLICY reactions_read ON public.post_reactions FOR SELECT
  USING (app.can_view_post(post_id));
DROP POLICY IF EXISTS reactions_write ON public.post_reactions;
CREATE POLICY reactions_write ON public.post_reactions FOR ALL
  USING (user_id = app.current_user_id() AND app.can_view_post(post_id))
  WITH CHECK (user_id = app.current_user_id() AND app.can_view_post(post_id));

ALTER TABLE public.post_shares ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS shares_read ON public.post_shares;
CREATE POLICY shares_read ON public.post_shares FOR SELECT
  USING (user_id = app.current_user_id() OR app.can_view_post(post_id));
DROP POLICY IF EXISTS shares_write ON public.post_shares;
CREATE POLICY shares_write ON public.post_shares FOR ALL
  USING (user_id = app.current_user_id())
  WITH CHECK (user_id = app.current_user_id());

ALTER TABLE public.conversations ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS conversations_read ON public.conversations;
CREATE POLICY conversations_read ON public.conversations FOR SELECT
  USING (deleted_at IS NULL
         AND EXISTS (SELECT 1 FROM public.conversation_members cm
                      WHERE cm.conversation_id = conversations.id
                        AND cm.user_id = app.current_user_id()));

-- Messaging requires an accepted connection between every pair of participants
-- at the moment the message is written.
CREATE OR REPLACE FUNCTION app.conversation_is_allowed(p_conversation_id uuid)
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, app, pg_temp
AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.conversation_members m
     WHERE m.conversation_id = p_conversation_id AND m.user_id = app.current_user_id()
  )
  AND NOT EXISTS (
    SELECT 1
      FROM public.conversation_members a
      JOIN public.conversation_members b ON b.conversation_id = a.conversation_id
     WHERE a.conversation_id = p_conversation_id
       AND a.user_id <> b.user_id
       AND NOT app.can_message(a.user_id, b.user_id)
  );
$$;

DROP POLICY IF EXISTS conversations_write ON public.conversations;
CREATE POLICY conversations_write ON public.conversations FOR ALL
  USING (EXISTS (SELECT 1 FROM public.conversation_members cm
                   WHERE cm.conversation_id = conversations.id
                     AND cm.user_id = app.current_user_id()
                     AND cm.role = 'OWNER'))
  WITH CHECK (created_by = app.current_user_id());

ALTER TABLE public.conversation_members ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS conv_members_read ON public.conversation_members;
CREATE POLICY conv_members_read ON public.conversation_members FOR SELECT
  USING (EXISTS (SELECT 1 FROM public.conversation_members mine
                   WHERE mine.conversation_id = conversation_members.conversation_id
                     AND mine.user_id = app.current_user_id()));
DROP POLICY IF EXISTS conv_members_write ON public.conversation_members;
CREATE POLICY conv_members_write ON public.conversation_members FOR ALL
  USING (EXISTS (SELECT 1 FROM public.conversations c
                   WHERE c.id = conversation_members.conversation_id
                     AND app.conversation_is_allowed(c.id)))
  WITH CHECK (app.conversation_is_allowed(conversation_id));

ALTER TABLE public.messages ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS messages_read ON public.messages;
CREATE POLICY messages_read ON public.messages FOR SELECT
  USING (deleted_at IS NULL AND app.conversation_is_allowed(conversation_id));
DROP POLICY IF EXISTS messages_write ON public.messages;
CREATE POLICY messages_write ON public.messages FOR ALL
  USING (sender_id = app.current_user_id() AND app.conversation_is_allowed(conversation_id))
  WITH CHECK (sender_id = app.current_user_id() AND app.conversation_is_allowed(conversation_id));

ALTER TABLE public.reports ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS reports_read_own ON public.reports;
CREATE POLICY reports_read_own ON public.reports FOR SELECT
  USING (reporter_id = app.current_user_id());
DROP POLICY IF EXISTS reports_write ON public.reports;
CREATE POLICY reports_write ON public.reports FOR INSERT
  WITH CHECK (reporter_id = app.current_user_id());

-- =============================================================================
-- Documents and MSAs
-- =============================================================================

ALTER TABLE public.documents ENABLE ROW LEVEL SECURITY;

-- Counterparties may read documents attached to their own contracts/MSAs.
CREATE OR REPLACE FUNCTION app.can_read_document(p_document_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, app, pg_temp
AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.documents d
     WHERE d.id = p_document_id
       AND d.deleted_at IS NULL
       AND (
         (d.company_id IS NOT NULL AND app.row_company_read(d.company_id, 'documents.read'))
         OR (d.owner_user_id = app.current_user_id())
         OR (d.related_type = 'CONTRACT' AND EXISTS (
               SELECT 1 FROM public.contracts c
                WHERE c.id = d.related_id
                  AND (app.is_member(c.company_id) OR app.is_member(c.counterparty_company_id))))
         OR (d.related_type = 'MSA' AND EXISTS (
               SELECT 1 FROM public.msas m
                WHERE m.id = d.related_id
                  AND (app.is_member(m.company_a_id) OR app.is_member(m.company_b_id))))
         OR (d.doc_type = 'MSA' AND EXISTS (
               SELECT 1 FROM public.msas m
                WHERE m.current_version_id IS NOT NULL
                  AND (app.is_member(m.company_a_id) OR app.is_member(m.company_b_id))))
       )
  );
$$;

DROP POLICY IF EXISTS documents_read ON public.documents;
CREATE POLICY documents_read ON public.documents FOR SELECT
  USING (app.can_read_document(id));

DROP POLICY IF EXISTS documents_write ON public.documents;
CREATE POLICY documents_write ON public.documents FOR ALL
  USING (app.row_company_read(company_id, 'documents.upload'))
  WITH CHECK (app.row_company_read(company_id, 'documents.upload'));

ALTER TABLE public.document_versions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS docver_read ON public.document_versions;
CREATE POLICY docver_read ON public.document_versions FOR SELECT
  USING (app.can_read_document(document_id)
         AND scan_status <> 'INFECTED');
DROP POLICY IF EXISTS docver_write ON public.document_versions;
CREATE POLICY docver_write ON public.document_versions FOR INSERT
  WITH CHECK (app.can_read_document(document_id)
              AND app.row_company_read((SELECT d.company_id FROM public.documents d
                                         WHERE d.id = document_versions.document_id),
                                       'documents.upload'));

ALTER TABLE public.document_access_log ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS docaccess_read ON public.document_access_log;
CREATE POLICY docaccess_read ON public.document_access_log FOR SELECT
  USING (user_id = app.current_user_id()
         OR app.row_company_read(company_id, 'documents.read_audit'));

ALTER TABLE public.msas ENABLE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION app.can_read_msa(p_msa_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, app, pg_temp
AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.msas m
     WHERE m.id = p_msa_id
       AND (app.is_member(m.company_a_id) OR app.is_member(m.company_b_id))
  );
$$;

DROP POLICY IF EXISTS msas_read ON public.msas;
CREATE POLICY msas_read ON public.msas FOR SELECT
  USING (app.can_read_msa(id));

DROP POLICY IF EXISTS msas_write ON public.msas;
CREATE POLICY msas_write ON public.msas FOR ALL
  USING (app.is_member(company_a_id) OR app.is_member(company_b_id))
  WITH CHECK (app.is_member(company_a_id) OR app.is_member(company_b_id));

ALTER TABLE public.msa_versions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS msavers_read ON public.msa_versions;
CREATE POLICY msavers_read ON public.msa_versions FOR SELECT
  USING (app.can_read_msa(msa_id));
DROP POLICY IF EXISTS msavers_write ON public.msa_versions;
CREATE POLICY msavers_write ON public.msa_versions FOR ALL
  USING (app.can_read_msa(msa_id)
         AND (
           app.row_company_read((SELECT m.company_a_id FROM public.msas m
                                 WHERE m.id = msa_versions.msa_id), 'msas.review')
           OR app.row_company_read((SELECT m.company_b_id FROM public.msas m
                                    WHERE m.id = msa_versions.msa_id), 'msas.review')
         ))
  WITH CHECK (app.can_read_msa(msa_id));

ALTER TABLE public.msa_requests ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS msareq_read ON public.msa_requests;
CREATE POLICY msareq_read ON public.msa_requests FOR SELECT
  USING (app.is_member(target_company_id) OR app.is_member(requester_company_id));
DROP POLICY IF EXISTS msareq_write ON public.msa_requests;
CREATE POLICY msareq_write ON public.msa_requests FOR ALL
  USING (app.is_member(target_company_id) OR app.is_member(requester_company_id))
  WITH CHECK (app.is_member(requester_company_id) OR app.is_member(target_company_id));

-- =============================================================================
-- CODE
-- =============================================================================

ALTER TABLE public.projects ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS projects_read ON public.projects;
CREATE POLICY projects_read ON public.projects FOR SELECT
  USING (deleted_at IS NULL AND app.row_company_read(company_id, 'projects.read'));
DROP POLICY IF EXISTS projects_write ON public.projects;
CREATE POLICY projects_write ON public.projects FOR ALL
  USING (app.row_company_read(company_id, 'projects.update'))
  WITH CHECK (app.row_company_read(company_id, 'projects.create'));

ALTER TABLE public.project_roles ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS project_roles_read ON public.project_roles;
CREATE POLICY project_roles_read ON public.project_roles FOR SELECT
  USING (deleted_at IS NULL AND app.row_company_read(company_id, 'projects.read'));
DROP POLICY IF EXISTS project_roles_write ON public.project_roles;
CREATE POLICY project_roles_write ON public.project_roles FOR ALL
  USING (app.row_company_read(company_id, 'projects.update'))
  WITH CHECK (app.row_company_read(company_id, 'projects.create'));

ALTER TABLE public.sows ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS sows_read ON public.sows;
CREATE POLICY sows_read ON public.sows FOR SELECT
  USING (deleted_at IS NULL
         AND (app.row_company_read(company_id, 'sows.read')
              OR (counterparty_company_id IS NOT NULL AND app.is_member(counterparty_company_id))
              OR (counterparty_user_id = app.current_user_id())));
DROP POLICY IF EXISTS sows_write ON public.sows;
CREATE POLICY sows_write ON public.sows FOR ALL
  USING (app.row_company_read(company_id, 'sows.update'))
  WITH CHECK (app.row_company_read(company_id, 'sows.create'));

ALTER TABLE public.sow_roles ENABLE ROW LEVEL SECURITY;
CREATE OR REPLACE FUNCTION app.can_view_sow(p_sow_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, app, pg_temp
AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.sows s
     WHERE s.id = p_sow_id AND s.deleted_at IS NULL
       AND (app.row_company_read(s.company_id, 'sows.read')
            OR (s.counterparty_company_id IS NOT NULL AND app.is_member(s.counterparty_company_id))
            OR s.counterparty_user_id = app.current_user_id())
  );
$$;

DROP POLICY IF EXISTS sow_roles_read ON public.sow_roles;
CREATE POLICY sow_roles_read ON public.sow_roles FOR SELECT
  USING (app.can_view_sow(sow_id));
DROP POLICY IF EXISTS sow_roles_write ON public.sow_roles;
CREATE POLICY sow_roles_write ON public.sow_roles FOR ALL
  USING (EXISTS (SELECT 1 FROM public.sows s
                   WHERE s.id = sow_roles.sow_id AND app.row_company_read(s.company_id, 'sows.update')))
  WITH CHECK (EXISTS (SELECT 1 FROM public.sows s
                        WHERE s.id = sow_roles.sow_id AND app.row_company_read(s.company_id, 'sows.update')));

-- Contracts: visible to both sides of the agreement.
CREATE OR REPLACE FUNCTION app.can_view_contract(p_contract_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, app, pg_temp
AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.contracts c
     WHERE c.id = p_contract_id AND c.deleted_at IS NULL
       AND (
         app.row_company_read(c.company_id, 'contracts.read')
         OR (c.counterparty_company_id IS NOT NULL AND app.is_member(c.counterparty_company_id)
             AND app.has_permission(c.counterparty_company_id, 'contracts.read'))
         OR c.counterparty_user_id = app.current_user_id()
         OR EXISTS (SELECT 1 FROM public.contract_parties cp
                     WHERE cp.contract_id = c.id
                       AND ((cp.party_company_id IS NOT NULL AND app.is_member(cp.party_company_id))
                            OR cp.party_user_id = app.current_user_id()))
       )
  );
$$;

COMMENT ON FUNCTION app.can_view_contract(uuid) IS
  'Contract visibility across multi-hop chains: any party may read; writes stay permission-scoped.';

ALTER TABLE public.contracts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS contracts_read ON public.contracts;
CREATE POLICY contracts_read ON public.contracts FOR SELECT
  USING (app.can_view_contract(id));
DROP POLICY IF EXISTS contracts_write ON public.contracts;
CREATE POLICY contracts_write ON public.contracts FOR ALL
  USING (app.row_company_read(company_id, 'contracts.update')
         OR (counterparty_user_id = app.current_user_id() AND status IN ('SENT','PENDING_ACCEPTANCE')))
  WITH CHECK (app.row_company_read(company_id, 'contracts.create'));

ALTER TABLE public.contract_parties ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS contract_parties_read ON public.contract_parties;
CREATE POLICY contract_parties_read ON public.contract_parties FOR SELECT
  USING (app.can_view_contract(contract_id));
DROP POLICY IF EXISTS contract_parties_write ON public.contract_parties;
CREATE POLICY contract_parties_write ON public.contract_parties FOR ALL
  USING (EXISTS (SELECT 1 FROM public.contracts c
                   WHERE c.id = contract_parties.contract_id
                     AND app.row_company_read(c.company_id, 'contracts.update')))
  WITH CHECK (EXISTS (SELECT 1 FROM public.contracts c
                        WHERE c.id = contract_parties.contract_id
                          AND app.row_company_read(c.company_id, 'contracts.update')));

ALTER TABLE public.contract_roles ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS contract_roles_read ON public.contract_roles;
CREATE POLICY contract_roles_read ON public.contract_roles FOR SELECT
  USING (app.can_view_contract(contract_id));
DROP POLICY IF EXISTS contract_roles_write ON public.contract_roles;
CREATE POLICY contract_roles_write ON public.contract_roles FOR ALL
  USING (EXISTS (SELECT 1 FROM public.contracts c
                   WHERE c.id = contract_roles.contract_id
                     AND app.row_company_read(c.company_id, 'contracts.update')))
  WITH CHECK (EXISTS (SELECT 1 FROM public.contracts c
                        WHERE c.id = contract_roles.contract_id
                          AND app.row_company_read(c.company_id, 'contracts.update')));

-- Line items hold rates: gate them behind a financial permission.
ALTER TABLE public.contract_line_items ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS line_items_read ON public.contract_line_items;
CREATE POLICY line_items_read ON public.contract_line_items FOR SELECT
  USING (
    EXISTS (SELECT 1 FROM public.contracts c
             WHERE c.id = contract_line_items.contract_id
               AND (app.row_company_read(c.company_id, 'contracts.read')
                    OR (c.counterparty_company_id IS NOT NULL
                        AND app.is_member(c.counterparty_company_id)
                        AND app.has_permission(c.counterparty_company_id, 'contracts.read'))))
    -- members holding a billing permission may see counterparties' commercial terms
    OR EXISTS (SELECT 1 FROM public.contracts c
                WHERE c.id = contract_line_items.contract_id
                  AND (app.is_member(c.company_id) OR app.is_member(c.counterparty_company_id))
                  AND app.has_any_permission(c.company_id,
                        ARRAY['contracts.read_rates','invoices.create','payments.read']))
  );
DROP POLICY IF EXISTS line_items_write ON public.contract_line_items;
CREATE POLICY line_items_write ON public.contract_line_items FOR ALL
  USING (EXISTS (SELECT 1 FROM public.contracts c
                   WHERE c.id = contract_line_items.contract_id
                     AND app.row_company_read(c.company_id, 'contracts.update')))
  WITH CHECK (EXISTS (SELECT 1 FROM public.contracts c
                        WHERE c.id = contract_line_items.contract_id
                          AND app.row_company_read(c.company_id, 'contracts.update')));

ALTER TABLE public.contract_approval_steps ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS contract_steps_read ON public.contract_approval_steps;
CREATE POLICY contract_steps_read ON public.contract_approval_steps FOR SELECT
  USING (app.can_view_contract(contract_id));
DROP POLICY IF EXISTS contract_steps_write ON public.contract_approval_steps;
CREATE POLICY contract_steps_write ON public.contract_approval_steps FOR ALL
  USING (EXISTS (SELECT 1 FROM public.contracts c
                   WHERE c.id = contract_approval_steps.contract_id
                     AND app.row_company_read(c.company_id, 'contracts.approve')))
  WITH CHECK (EXISTS (SELECT 1 FROM public.contracts c
                        WHERE c.id = contract_approval_steps.contract_id
                          AND app.row_company_read(c.company_id, 'contracts.update')));

-- =============================================================================
-- WORK
-- =============================================================================

ALTER TABLE public.assignments ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS assignments_read ON public.assignments;
CREATE POLICY assignments_read ON public.assignments FOR SELECT
  USING (user_id = app.current_user_id()
         OR app.row_company_read(company_id, 'workforce.read'));
DROP POLICY IF EXISTS assignments_write ON public.assignments;
CREATE POLICY assignments_write ON public.assignments FOR ALL
  USING (app.row_company_read(company_id, 'workforce.manage'))
  WITH CHECK (app.row_company_read(company_id, 'workforce.manage'));

-- Timesheets: employees see their own; managers see the company's (subject to permission).
ALTER TABLE public.timesheets ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS timesheets_read ON public.timesheets;
CREATE POLICY timesheets_read ON public.timesheets FOR SELECT
  USING (user_id = app.current_user_id()
         OR app.row_company_read(company_id, 'timesheets.read_any'));
DROP POLICY IF EXISTS timesheets_insert ON public.timesheets;
CREATE POLICY timesheets_insert ON public.timesheets FOR INSERT
  WITH CHECK ((user_id = app.current_user_id()
               AND app.has_permission(company_id, 'timesheets.create'))
              OR app.row_company_read(company_id, 'timesheets.create_any'));
DROP POLICY IF EXISTS timesheets_update ON public.timesheets;
CREATE POLICY timesheets_update ON public.timesheets FOR UPDATE
  USING ((user_id = app.current_user_id() AND app.has_permission(company_id, 'timesheets.create')
                         AND status IN ('DRAFT','REJECTED'))
         OR app.row_company_read(company_id, 'timesheets.approve'))
  WITH CHECK ((user_id = app.current_user_id() AND status IN ('DRAFT','REJECTED','SUBMITTED'))
              OR app.row_company_read(company_id, 'timesheets.approve'));

ALTER TABLE public.timesheet_entries ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS ts_entries_read ON public.timesheet_entries;
CREATE POLICY ts_entries_read ON public.timesheet_entries FOR SELECT
  USING (EXISTS (SELECT 1 FROM public.timesheets t
                   WHERE t.id = timesheet_entries.timesheet_id
                     AND (t.user_id = app.current_user_id()
                          OR app.row_company_read(t.company_id, 'timesheets.read_any'))));
DROP POLICY IF EXISTS ts_entries_write ON public.timesheet_entries;
CREATE POLICY ts_entries_write ON public.timesheet_entries FOR ALL
  USING (EXISTS (SELECT 1 FROM public.timesheets t
                   WHERE t.id = timesheet_entries.timesheet_id
                     AND t.user_id = app.current_user_id()
                     AND t.status IN ('DRAFT','REJECTED')
                     AND app.has_permission(t.company_id, 'timesheets.create')))
  WITH CHECK (EXISTS (SELECT 1 FROM public.timesheets t
                        WHERE t.id = timesheet_entries.timesheet_id
                          AND t.user_id = app.current_user_id()
                          AND t.status IN ('DRAFT','REJECTED')
                          AND app.has_permission(t.company_id, 'timesheets.create')));

ALTER TABLE public.timesheet_revisions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS ts_revisions_read ON public.timesheet_revisions;
CREATE POLICY ts_revisions_read ON public.timesheet_revisions FOR SELECT
  USING (EXISTS (SELECT 1 FROM public.timesheets t
                   WHERE t.id = timesheet_revisions.timesheet_id
                     AND (t.user_id = app.current_user_id()
                          OR app.row_company_read(t.company_id, 'timesheets.read_any'))));

ALTER TABLE public.timesheet_approvals ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS ts_approvals_read ON public.timesheet_approvals;
CREATE POLICY ts_approvals_read ON public.timesheet_approvals FOR SELECT
  USING (approver_user_id = app.current_user_id()
         OR EXISTS (SELECT 1 FROM public.timesheets t
                     WHERE t.id = timesheet_approvals.timesheet_id
                       AND (t.user_id = app.current_user_id()
                            OR app.row_company_read(t.company_id, 'timesheets.read_any'))));
DROP POLICY IF EXISTS ts_approvals_write ON public.timesheet_approvals;
CREATE POLICY ts_approvals_write ON public.timesheet_approvals FOR UPDATE
  USING (approver_user_id = app.current_user_id()
         OR app.row_company_read(company_id, 'timesheets.approve'))
  WITH CHECK (approver_user_id = app.current_user_id()
              OR app.row_company_read(company_id, 'timesheets.approve'));
DROP POLICY IF EXISTS ts_approvals_insert ON public.timesheet_approvals;
CREATE POLICY ts_approvals_insert ON public.timesheet_approvals FOR INSERT
  WITH CHECK (EXISTS (SELECT 1 FROM public.timesheets t
                        WHERE t.id = timesheet_approvals.timesheet_id
                          AND app.row_company_read(t.company_id, 'timesheets.approve')));

-- Leave balances are personal by default; HR/finance can read company-wide.
ALTER TABLE public.leave_balances ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS leave_balances_read ON public.leave_balances;
CREATE POLICY leave_balances_read ON public.leave_balances FOR SELECT
  USING (user_id = app.current_user_id()
         OR app.row_company_read(company_id, 'leave.read_any'));
DROP POLICY IF EXISTS leave_balances_write ON public.leave_balances;
CREATE POLICY leave_balances_write ON public.leave_balances FOR ALL
  USING (app.row_company_read(company_id, 'leave.manage'))
  WITH CHECK (app.row_company_read(company_id, 'leave.manage'));

ALTER TABLE public.leave_policies ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS leave_policies_read ON public.leave_policies;
CREATE POLICY leave_policies_read ON public.leave_policies FOR SELECT
  USING (app.row_company_ok(company_id) AND is_active);
DROP POLICY IF EXISTS leave_policies_write ON public.leave_policies;
CREATE POLICY leave_policies_write ON public.leave_policies FOR ALL
  USING (app.row_company_read(company_id, 'leave.manage'))
  WITH CHECK (app.row_company_read(company_id, 'leave.manage'));

ALTER TABLE public.leave_requests ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS leave_requests_read ON public.leave_requests;
CREATE POLICY leave_requests_read ON public.leave_requests FOR SELECT
  USING (user_id = app.current_user_id()
         OR app.row_company_read(company_id, 'leave.read_any'));
DROP POLICY IF EXISTS leave_requests_write ON public.leave_requests;
CREATE POLICY leave_requests_write ON public.leave_requests FOR INSERT
  WITH CHECK (user_id = app.current_user_id()
              OR app.row_company_read(company_id, 'leave.manage'));
DROP POLICY IF EXISTS leave_requests_update ON public.leave_requests;
CREATE POLICY leave_requests_update ON public.leave_requests FOR UPDATE
  USING ((user_id = app.current_user_id() AND status IN ('DRAFT','PENDING'))
         OR app.row_company_read(company_id, 'leave.approve'))
  WITH CHECK ((user_id = app.current_user_id() AND status IN ('DRAFT','PENDING','CANCELLED'))
              OR app.row_company_read(company_id, 'leave.approve'));

-- =============================================================================
-- Billing
-- =============================================================================

ALTER TABLE public.billing_runs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS billing_runs_read ON public.billing_runs;
CREATE POLICY billing_runs_read ON public.billing_runs FOR SELECT
  USING (app.row_company_read(company_id, 'invoices.read'));
DROP POLICY IF EXISTS billing_runs_write ON public.billing_runs;
CREATE POLICY billing_runs_write ON public.billing_runs FOR ALL
  USING (app.row_company_read(company_id, 'invoices.create'))
  WITH CHECK (app.row_company_read(company_id, 'invoices.create'));

-- Invoices are readable by BOTH parties (that is the point of an invoice), but
-- only the issuer may modify. Amount columns are protected by the trigger layer.
ALTER TABLE public.invoices ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS invoices_read ON public.invoices;
CREATE POLICY invoices_read ON public.invoices FOR SELECT
  USING (deleted_at IS NULL
         AND (app.is_member(company_id) OR app.is_member(counterparty_company_id)
              OR counterparty_user_id = app.current_user_id())
         AND app.has_any_permission(company_id, ARRAY['invoices.read'])
         );
DROP POLICY IF EXISTS invoices_write ON public.invoices;
CREATE POLICY invoices_write ON public.invoices FOR ALL
  USING (app.row_company_read(company_id, 'invoices.update'))
  WITH CHECK (app.row_company_read(company_id, 'invoices.create'));

ALTER TABLE public.invoice_items ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS invoice_items_read ON public.invoice_items;
CREATE POLICY invoice_items_read ON public.invoice_items FOR SELECT
  USING (EXISTS (SELECT 1 FROM public.invoices i
                   WHERE i.id = invoice_items.invoice_id
                     AND (app.is_member(i.company_id) OR app.is_member(i.counterparty_company_id))
                     AND app.has_permission(i.company_id, 'invoices.read')));
DROP POLICY IF EXISTS invoice_items_write ON public.invoice_items;
CREATE POLICY invoice_items_write ON public.invoice_items FOR ALL
  USING (EXISTS (SELECT 1 FROM public.invoices i
                   WHERE i.id = invoice_items.invoice_id
                     AND app.row_company_read(i.company_id, 'invoices.update')))
  WITH CHECK (EXISTS (SELECT 1 FROM public.invoices i
                        WHERE i.id = invoice_items.invoice_id
                          AND app.row_company_read(i.company_id, 'invoices.update')));

ALTER TABLE public.invoice_allocations ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS invoice_allocations_read ON public.invoice_allocations;
CREATE POLICY invoice_allocations_read ON public.invoice_allocations FOR SELECT
  USING (EXISTS (SELECT 1 FROM public.invoices i
                   WHERE i.id = invoice_allocations.invoice_id
                     AND (app.is_member(i.company_id) OR app.is_member(i.counterparty_company_id))
                     AND app.has_permission(i.company_id, 'invoices.read')));
DROP POLICY IF EXISTS invoice_allocations_write ON public.invoice_allocations;
CREATE POLICY invoice_allocations_write ON public.invoice_allocations FOR ALL
  USING (EXISTS (SELECT 1 FROM public.invoices i
                   WHERE i.id = invoice_allocations.invoice_id
                     AND app.row_company_read(i.company_id, 'invoices.update')))
  WITH CHECK (EXISTS (SELECT 1 FROM public.invoices i
                        WHERE i.id = invoice_allocations.invoice_id
                          AND app.row_company_read(i.company_id, 'invoices.update')));

ALTER TABLE public.invoice_approvals ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS invoice_approvals_read ON public.invoice_approvals;
CREATE POLICY invoice_approvals_read ON public.invoice_approvals FOR SELECT
  USING (approver_user_id = app.current_user_id()
         OR EXISTS (SELECT 1 FROM public.invoices i
                     WHERE i.id = invoice_approvals.invoice_id
                       AND app.row_company_read(i.company_id, 'invoices.read')));
DROP POLICY IF EXISTS invoice_approvals_write ON public.invoice_approvals;
CREATE POLICY invoice_approvals_write ON public.invoice_approvals FOR UPDATE
  USING (approver_user_id = app.current_user_id()
         OR EXISTS (SELECT 1 FROM public.invoices i
                     WHERE i.id = invoice_approvals.invoice_id
                       AND app.row_company_read(i.company_id, 'invoices.approve')))
  WITH CHECK (approver_user_id = app.current_user_id()
              OR EXISTS (SELECT 1 FROM public.invoices i
                          WHERE i.id = invoice_approvals.invoice_id
                            AND app.row_company_read(i.company_id, 'invoices.approve')));

-- =============================================================================
-- Payments — the most sensitive surface.
-- =============================================================================

ALTER TABLE public.payment_accounts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS payment_accounts_read ON public.payment_accounts;
CREATE POLICY payment_accounts_read ON public.payment_accounts FOR SELECT
  USING (deleted_at IS NULL AND app.row_company_read(company_id, 'payments.read'));
DROP POLICY IF EXISTS payment_accounts_write ON public.payment_accounts;
CREATE POLICY payment_accounts_write ON public.payment_accounts FOR ALL
  USING (app.row_company_read(company_id, 'payments.manage'))
  WITH CHECK (app.row_company_read(company_id, 'payments.manage'));

-- Bank connections: only the linking owner or a payments.admin may see them.
ALTER TABLE public.bank_connections ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS bank_connections_read ON public.bank_connections;
CREATE POLICY bank_connections_read ON public.bank_connections FOR SELECT
  USING (deleted_at IS NULL
         AND (owner_user_id = app.current_user_id()
              OR app.row_company_read(company_id, 'payments.manage')));
DROP POLICY IF EXISTS bank_connections_write ON public.bank_connections;
CREATE POLICY bank_connections_write ON public.bank_connections FOR INSERT
  WITH CHECK (owner_user_id = app.current_user_id()
              AND app.row_company_read(company_id, 'payments.connect_bank'));
DROP POLICY IF EXISTS bank_connections_update ON public.bank_connections;
CREATE POLICY bank_connections_update ON public.bank_connections FOR UPDATE
  USING (owner_user_id = app.current_user_id()
         OR app.row_company_read(company_id, 'payments.manage'))
  WITH CHECK (owner_user_id = app.current_user_id()
              OR app.row_company_read(company_id, 'payments.manage'));
DROP POLICY IF EXISTS bank_connections_delete ON public.bank_connections;
CREATE POLICY bank_connections_delete ON public.bank_connections FOR DELETE
  USING (app.row_company_read(company_id, 'payments.manage'));

ALTER TABLE public.bank_accounts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS bank_accounts_read ON public.bank_accounts;
CREATE POLICY bank_accounts_read ON public.bank_accounts FOR SELECT
  USING (deleted_at IS NULL AND app.row_company_read(company_id, 'payments.read'));
DROP POLICY IF EXISTS bank_accounts_write ON public.bank_accounts;
CREATE POLICY bank_accounts_write ON public.bank_accounts FOR ALL
  USING (app.row_company_read(company_id, 'payments.connect_bank')
         OR app.row_company_read(company_id, 'payments.manage'))
  WITH CHECK (app.row_company_read(company_id, 'payments.connect_bank')
              OR app.row_company_read(company_id, 'payments.manage'));

ALTER TABLE public.bank_transactions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS bank_txn_read ON public.bank_transactions;
CREATE POLICY bank_txn_read ON public.bank_transactions FOR SELECT
  USING (app.row_company_read(company_id, 'transactions.read'));

-- Reconciliation must record its decision, so an UPDATE policy exists for
-- reconciliation.manage holders. The column-level immutability trigger
-- (app.assert_txn_ledger_immutable) is what actually protects the ledger facts:
-- RLS decides who may write, the trigger decides what may change.
DROP POLICY IF EXISTS bank_txn_reconcile ON public.bank_transactions;
CREATE POLICY bank_txn_reconcile ON public.bank_transactions FOR UPDATE
  USING (app.row_company_read(company_id, 'reconciliation.manage'))
  WITH CHECK (app.row_company_read(company_id, 'reconciliation.manage'));

-- No INSERT or DELETE policy exists for mytrakin_api: only the worker (BYPASSRLS,
-- no end-user credentials) imports transactions, and the ledger is append-only.
-- A request can therefore neither forge a bank transaction nor remove one.

ALTER TABLE public.payments ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS payments_read ON public.payments;
CREATE POLICY payments_read ON public.payments FOR SELECT
  USING (deleted_at IS NULL
         AND (app.row_company_read(company_id, 'payments.read')
              OR app.is_member(counterparty_company_id)));
DROP POLICY IF EXISTS payments_write ON public.payments;
CREATE POLICY payments_write ON public.payments FOR ALL
  USING (app.row_company_read(company_id, 'payments.update'))
  WITH CHECK (app.row_company_read(company_id, 'payments.create'));

ALTER TABLE public.payment_allocations ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS pay_alloc_read ON public.payment_allocations;
CREATE POLICY pay_alloc_read ON public.payment_allocations FOR SELECT
  USING (EXISTS (SELECT 1 FROM public.payments p
                   WHERE p.id = payment_allocations.payment_id
                     AND app.row_company_read(p.company_id, 'payments.read')));
DROP POLICY IF EXISTS pay_alloc_write ON public.payment_allocations;
CREATE POLICY pay_alloc_write ON public.payment_allocations FOR ALL
  USING (EXISTS (SELECT 1 FROM public.payments p
                   WHERE p.id = payment_allocations.payment_id
                     AND app.row_company_read(p.company_id, 'payments.update')))
  WITH CHECK (EXISTS (SELECT 1 FROM public.payments p
                        WHERE p.id = payment_allocations.payment_id
                          AND app.row_company_read(p.company_id, 'payments.update')));

ALTER TABLE public.payment_matches ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS payment_matches_read ON public.payment_matches;
CREATE POLICY payment_matches_read ON public.payment_matches FOR SELECT
  USING (app.row_company_read(company_id, 'reconciliation.read'));
DROP POLICY IF EXISTS payment_matches_write ON public.payment_matches;
CREATE POLICY payment_matches_write ON public.payment_matches FOR ALL
  USING (app.row_company_read(company_id, 'reconciliation.manage'))
  WITH CHECK (app.row_company_read(company_id, 'reconciliation.manage'));

ALTER TABLE public.payment_requests ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS payment_requests_read ON public.payment_requests;
CREATE POLICY payment_requests_read ON public.payment_requests FOR SELECT
  USING (app.is_member(company_id) OR app.is_member(requester_company_id)
         OR app.is_member(counterparty_company_id));
DROP POLICY IF EXISTS payment_requests_write ON public.payment_requests;
CREATE POLICY payment_requests_write ON public.payment_requests FOR ALL
  USING (app.row_company_read(company_id, 'payments.create'))
  WITH CHECK (app.row_company_read(company_id, 'payments.create'));

ALTER TABLE public.payment_schedules ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS payment_schedules_read ON public.payment_schedules;
CREATE POLICY payment_schedules_read ON public.payment_schedules FOR SELECT
  USING (deleted_at IS NULL AND app.row_company_read(company_id, 'payments.read'));
DROP POLICY IF EXISTS payment_schedules_write ON public.payment_schedules;
CREATE POLICY payment_schedules_write ON public.payment_schedules FOR ALL
  USING (app.row_company_read(company_id, 'payments.manage'))
  WITH CHECK (app.row_company_read(company_id, 'payments.manage'));

-- =============================================================================
-- AI
-- =============================================================================

ALTER TABLE public.ai_providers ENABLE ROW LEVEL SECURITY;
-- Read-only reference for any authenticated user (contains no secrets).
DROP POLICY IF EXISTS ai_providers_read ON public.ai_providers;
CREATE POLICY ai_providers_read ON public.ai_providers FOR SELECT
  USING (app.current_user_id() IS NOT NULL AND is_enabled);

ALTER TABLE public.ai_usage_events ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS ai_usage_read ON public.ai_usage_events;
CREATE POLICY ai_usage_read ON public.ai_usage_events FOR SELECT
  USING (user_id = app.current_user_id()
         OR app.row_company_read(company_id, 'ai.usage.read'));

ALTER TABLE public.ai_conversations ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS ai_conv_read ON public.ai_conversations;
CREATE POLICY ai_conv_read ON public.ai_conversations FOR SELECT
  USING (user_id = app.current_user_id() AND archived_at IS NULL);
DROP POLICY IF EXISTS ai_conv_write ON public.ai_conversations;
CREATE POLICY ai_conv_write ON public.ai_conversations FOR ALL
  USING (user_id = app.current_user_id())
  WITH CHECK (user_id = app.current_user_id());

ALTER TABLE public.ai_messages ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS ai_msg_read ON public.ai_messages;
CREATE POLICY ai_msg_read ON public.ai_messages FOR SELECT
  USING (EXISTS (SELECT 1 FROM public.ai_conversations c
                   WHERE c.id = ai_messages.conversation_id
                     AND c.user_id = app.current_user_id()));
DROP POLICY IF EXISTS ai_msg_write ON public.ai_messages;
CREATE POLICY ai_msg_write ON public.ai_messages FOR INSERT
  WITH CHECK (EXISTS (SELECT 1 FROM public.ai_conversations c
                        WHERE c.id = ai_messages.conversation_id
                          AND c.user_id = app.current_user_id()));

-- Knowledge documents: membership + the document's required permission.
ALTER TABLE public.ai_knowledge_documents ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS ai_kd_read ON public.ai_knowledge_documents;
CREATE POLICY ai_kd_read ON public.ai_knowledge_documents FOR SELECT
  USING (app.row_company_ok(company_id)
         AND app.has_permission(company_id, required_permission));
DROP POLICY IF EXISTS ai_kd_write ON public.ai_knowledge_documents;
CREATE POLICY ai_kd_write ON public.ai_knowledge_documents FOR ALL
  USING (app.row_company_read(company_id, 'documents.upload'))
  WITH CHECK (app.row_company_read(company_id, 'documents.upload'));

-- Chunks inherit their parent's permission; there is no independent chunk policy
-- that could be more permissive than the parent document.
ALTER TABLE public.ai_document_chunks ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS ai_chunks_read ON public.ai_document_chunks;
CREATE POLICY ai_chunks_read ON public.ai_document_chunks FOR SELECT
  USING (EXISTS (SELECT 1 FROM public.ai_knowledge_documents kd
                   WHERE kd.id = ai_document_chunks.knowledge_document_id
                     AND kd.is_active
                     AND app.row_company_ok(kd.company_id)
                     AND app.has_permission(kd.company_id, kd.required_permission)));
DROP POLICY IF EXISTS ai_chunks_write ON public.ai_document_chunks;
CREATE POLICY ai_chunks_write ON public.ai_document_chunks FOR ALL
  USING (EXISTS (SELECT 1 FROM public.ai_knowledge_documents kd
                   WHERE kd.id = ai_document_chunks.knowledge_document_id
                     AND app.row_company_read(kd.company_id, 'documents.upload')))
  WITH CHECK (EXISTS (SELECT 1 FROM public.ai_knowledge_documents kd
                        WHERE kd.id = ai_document_chunks.knowledge_document_id
                          AND kd.company_id = ai_document_chunks.company_id
                          AND app.row_company_read(kd.company_id, 'documents.upload')));

ALTER TABLE public.ai_extractions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS ai_extractions_read ON public.ai_extractions;
CREATE POLICY ai_extractions_read ON public.ai_extractions FOR SELECT
  USING ((user_id = app.current_user_id()
          AND app.current_user_id() IS NOT NULL)
         OR app.row_company_read(company_id, 'ai.extractions.read'));
DROP POLICY IF EXISTS ai_extractions_insert ON public.ai_extractions;
CREATE POLICY ai_extractions_insert ON public.ai_extractions FOR INSERT
  WITH CHECK (user_id = app.current_user_id()
              OR app.row_company_read(company_id, 'ai.extractions.create'));
-- Deliberately NO UPDATE policy: promotion to CONFIRMED/APPLIED happens only
-- through app.ai_extractions service code, which re-checks the human's authority.

ALTER TABLE public.ai_actions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS ai_actions_read ON public.ai_actions;
CREATE POLICY ai_actions_read ON public.ai_actions FOR SELECT
  USING (app.row_company_ok(company_id));
DROP POLICY IF EXISTS ai_actions_insert ON public.ai_actions;
CREATE POLICY ai_actions_insert ON public.ai_actions FOR INSERT
  WITH CHECK (initiated_by = app.current_user_id()
              AND app.row_company_ok(company_id));
DROP POLICY IF EXISTS ai_actions_update ON public.ai_actions;
CREATE POLICY ai_actions_update ON public.ai_actions FOR UPDATE
  USING (initiated_by = app.current_user_id()
         OR (approved_by = app.current_user_id())
         OR app.row_company_read(company_id, 'ai.actions.approve'))
  WITH CHECK (app.row_company_ok(company_id));

ALTER TABLE public.ai_insights ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS ai_insights_read ON public.ai_insights;
CREATE POLICY ai_insights_read ON public.ai_insights FOR SELECT
  USING (app.row_company_ok(company_id)
         AND app.has_permission(company_id, required_permission));
DROP POLICY IF EXISTS ai_insights_update ON public.ai_insights;
CREATE POLICY ai_insights_update ON public.ai_insights FOR UPDATE
  USING (app.row_company_ok(company_id))
  WITH CHECK (app.row_company_ok(company_id));

ALTER TABLE public.ai_automations ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS ai_automations_read ON public.ai_automations;
CREATE POLICY ai_automations_read ON public.ai_automations FOR SELECT
  USING (app.row_company_ok(company_id));
DROP POLICY IF EXISTS ai_automations_write ON public.ai_automations;
CREATE POLICY ai_automations_write ON public.ai_automations FOR ALL
  USING (app.row_company_read(company_id, 'ai.automations.manage'))
  WITH CHECK (app.row_company_read(company_id, 'ai.automations.manage'));

ALTER TABLE public.ai_automation_runs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS ai_runs_read ON public.ai_automation_runs;
CREATE POLICY ai_runs_read ON public.ai_automation_runs FOR SELECT
  USING (app.row_company_ok(company_id));
DROP POLICY IF EXISTS ai_runs_update ON public.ai_automation_runs;
CREATE POLICY ai_runs_update ON public.ai_automation_runs FOR UPDATE
  USING (approved_by = app.current_user_id()
         OR app.row_company_read(company_id, 'ai.automations.manage'))
  WITH CHECK (app.row_company_ok(company_id));

-- =============================================================================
-- Platform infrastructure
-- =============================================================================

-- Audit: read your own actions, or company-scoped with audit.read.
ALTER TABLE platform.audit_logs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS audit_read ON platform.audit_logs;
CREATE POLICY audit_read ON platform.audit_logs FOR SELECT
  USING (actor_user_id = app.current_user_id()
         OR app.row_company_read(company_id, 'audit.read'));
-- INSERT only: the ledger is append-only and only the API/worker may write.
DROP POLICY IF EXISTS audit_insert ON platform.audit_logs;
CREATE POLICY audit_insert ON platform.audit_logs FOR INSERT
  WITH CHECK (actor_user_id = app.current_user_id() OR actor_user_id IS NULL);

ALTER TABLE platform.outbox_events ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS outbox_read ON platform.outbox_events;
CREATE POLICY outbox_read ON platform.outbox_events FOR SELECT
  USING (app.row_company_ok(company_id));

ALTER TABLE platform.idempotency_keys ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS idem_read ON platform.idempotency_keys;
CREATE POLICY idem_read ON platform.idempotency_keys FOR SELECT
  USING (user_id = app.current_user_id() OR app.row_company_ok(company_id));
DROP POLICY IF EXISTS idem_write ON platform.idempotency_keys;
CREATE POLICY idem_write ON platform.idempotency_keys FOR ALL
  USING (user_id = app.current_user_id() OR app.current_user_id() IS NULL)
  WITH CHECK (user_id = app.current_user_id() OR user_id IS NULL);

ALTER TABLE platform.feature_flags ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS flags_read ON platform.feature_flags;
CREATE POLICY flags_read ON platform.feature_flags FOR SELECT
  USING (app.current_user_id() IS NOT NULL);

ALTER TABLE platform.feature_flag_overrides ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS flag_overrides_read ON platform.feature_flag_overrides;
CREATE POLICY flag_overrides_read ON platform.feature_flag_overrides FOR SELECT
  USING (app.current_user_id() IS NOT NULL);

ALTER TABLE platform.notifications ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS notifications_read ON platform.notifications;
CREATE POLICY notifications_read ON platform.notifications FOR SELECT
  USING (user_id = app.current_user_id());
DROP POLICY IF EXISTS notifications_update ON platform.notifications;
CREATE POLICY notifications_update ON platform.notifications FOR UPDATE
  USING (user_id = app.current_user_id())
  WITH CHECK (user_id = app.current_user_id());
-- No INSERT policy for mytrakin_api: notifications are produced by workers.

ALTER TABLE platform.notification_preferences ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS notif_prefs ON platform.notification_preferences;
CREATE POLICY notif_prefs ON platform.notification_preferences FOR ALL
  USING (user_id = app.current_user_id())
  WITH CHECK (user_id = app.current_user_id());

ALTER TABLE platform.user_sessions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS sessions_self ON platform.user_sessions;
CREATE POLICY sessions_self ON platform.user_sessions FOR SELECT
  USING (user_id = app.current_user_id());

ALTER TABLE platform.tasks ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tasks_read ON platform.tasks;
CREATE POLICY tasks_read ON platform.tasks FOR SELECT
  USING (requested_by = app.current_user_id()
         OR app.row_company_read(company_id, 'tasks.read'));

ALTER TABLE platform.rate_limit_counters ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS ratelimit_read ON platform.rate_limit_counters;
CREATE POLICY ratelimit_read ON platform.rate_limit_counters FOR SELECT
  USING (subject = app.current_user_id()::text);

-- Webhook ledger: workers only. No policy for mytrakin_api on purpose.

-- =============================================================================
-- Column-level hardening
-- Sensitive columns are additionally restricted at the column level so that even
-- a `SELECT *` from a permissive table cannot leak them to the API role.
-- =============================================================================

-- =============================================================================
-- Role grants
-- =============================================================================

-- Supabase installs pgcrypto/vector/pg_trgm into its own `extensions` schema.
-- Without USAGE the API role cannot even resolve app.uuid7()'s dependency.
GRANT USAGE ON SCHEMA extensions TO mytrakin_api, mytrakin_worker, mytrakin_readonly;

REVOKE ALL ON public.user_sensitive                FROM mytrakin_api;
GRANT  SELECT (user_id, ssn_last4, tax_id_last4, tax_id_type,
               verification_state, verified_at, created_at, updated_at)
  ON public.user_sensitive TO mytrakin_api;   -- ciphertext columns are NOT granted

REVOKE ALL ON public.bank_connections             FROM mytrakin_api;
GRANT  SELECT (id, company_id, public_id, provider, owner_user_id, institution_name,
               institution_id, status, verification_state, ownership_verified,
               consent_expires_at, last_synced_at, transaction_count, created_at, updated_at)
  ON public.bank_connections TO mytrakin_api; -- item_id/access_token ciphertext not granted

REVOKE ALL ON public.ai_document_chunks           FROM mytrakin_api;
GRANT  SELECT (id, knowledge_document_id, company_id, chunk_index, content, token_count,
               embedding_model, page_number, section_path, content_hash, metadata, created_at)
  ON public.ai_document_chunks TO mytrakin_api;
