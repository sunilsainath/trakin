-- =============================================================================
-- MyTrakin :: 0004_rbac_companies.sql
-- Companies, memberships, roles, permissions, invitations.
-- Company membership grants context; permissions grant authority.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- companies
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.companies (
  id                  uuid PRIMARY KEY DEFAULT app.uuid7(),
  public_id           text NOT NULL UNIQUE,
  legal_name          text NOT NULL,
  display_name        text NOT NULL,
  dba                 text,
  country_code        char(2),
  address_line1       text,
  address_line2       text,
  city                text,
  region              text,
  postal_code         text,
  tax_id_type         text CHECK (tax_id_type IS NULL
                                   OR tax_id_type IN ('EIN','SSN','ITIN','UNKNOWN')),
  tax_id_last4        char(4),              -- masked; full TIN never stored in plain text
  tax_id_encrypted    bytea,
  w9_document_id      uuid,                -- FK added in 0006 (documents)
  w9_extraction_id    uuid,                -- FK added in 0007 (ai_extractions)
  verification_state  public.verification_state NOT NULL DEFAULT 'UNVERIFIED',
  default_currency    char(3) NOT NULL DEFAULT 'USD',
  fiscal_year_start   smallint NOT NULL DEFAULT 1 CHECK (fiscal_year_start BETWEEN 1 AND 12),
  settings            jsonb NOT NULL DEFAULT '{}'::jsonb,   -- policy toggles, see docs
  status              text NOT NULL DEFAULT 'ACTIVE'
                        CHECK (status IN ('PENDING_VERIFICATION','ACTIVE','SUSPENDED','CLOSED')),
  created_by          uuid NOT NULL REFERENCES public.users(id) ON DELETE RESTRICT,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  deleted_at          timestamptz,
  CONSTRAINT ck_company_public_id CHECK (public_id ~ '^CO[0-9A-HJKMNP-TV-Z]{8}$'),
  CONSTRAINT ck_company_currency CHECK (default_currency ~ '^[A-Z]{3}$')
);

COMMENT ON TABLE public.companies IS
  'Legal entity. Requires a W-9 before verification; OCR success is never treated as legal verification.';
COMMENT ON COLUMN public.companies.settings IS
  'Company policy: invoice terms, timesheet locks, segregation of duties, auto-reconcile threshold, leave defaults.';

CREATE INDEX IF NOT EXISTS ix_companies_name_trgm
  ON public.companies USING gin (display_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_companies_status ON public.companies (status) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_companies_legal_trgm
  ON public.companies USING gin (legal_name gin_trgm_ops);

CREATE OR REPLACE FUNCTION app.assign_company_public_id() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_try int := 0; v_id text;
BEGIN

  LOOP
    v_id := app.gen_public_id('CO', 8);
    EXIT WHEN NOT EXISTS (SELECT 1 FROM public.companies WHERE public_id = v_id);
    v_try := v_try + 1;
    IF v_try > 5 THEN RAISE EXCEPTION 'could not allocate company public_id'; END IF;
  END LOOP;
  NEW.public_id := v_id;
  RETURN NEW;
END
$$;

SELECT app.attach_triggers('public.companies'::regclass);
DROP TRIGGER IF EXISTS trg_companies_public_id ON public.companies;
CREATE TRIGGER trg_companies_public_id BEFORE INSERT ON public.companies
  FOR EACH ROW WHEN (NEW.public_id IS NULL)
  EXECUTE FUNCTION app.assign_company_public_id();

-- -----------------------------------------------------------------------------
-- permissions — the catalogue. Seeded by migration; never created at runtime.
-- Stored as data so roles are configurable without a deploy.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.permissions (
  key          text PRIMARY KEY,
  module       text NOT NULL,
  action       text NOT NULL,
  description  text NOT NULL DEFAULT '',
  -- Risk class drives extra controls: SoD checks and mandatory confirmation.
  sensitivity  text NOT NULL DEFAULT 'STANDARD'
                 CHECK (sensitivity IN ('STANDARD','SENSITIVE','FINANCIAL','DESTRUCTIVE')),
  requires_approval boolean NOT NULL DEFAULT false,
  created_at   timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.permissions IS
  'Permission catalogue, e.g. contracts.approve. Immutable keys; only descriptions/sensitivity are editable.';
COMMENT ON COLUMN public.permissions.sensitivity IS
  'FINANCIAL/DESTRUCTIVE permissions trigger segregation-of-duties checks and stricter audit.';

CREATE INDEX IF NOT EXISTS ix_permissions_module ON public.permissions (module, action);

-- -----------------------------------------------------------------------------
-- company_roles — a named bundle of permissions within one company.
-- `is_system` marks the templates seeded on company creation (SUPER_ADMIN, ...).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.company_roles (
  id           uuid PRIMARY KEY DEFAULT app.uuid7(),
  company_id   uuid NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
  public_id    text NOT NULL UNIQUE,
  key          text NOT NULL,
  name         text NOT NULL,
  description  text NOT NULL DEFAULT '',
  is_system    boolean NOT NULL DEFAULT false,
  is_assignable boolean NOT NULL DEFAULT true,   -- system roles may be unassignable
  created_by   uuid REFERENCES public.users(id) ON DELETE SET NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  deleted_at   timestamptz,
  UNIQUE (company_id, key),
  CONSTRAINT ck_role_public_id CHECK (public_id ~ '^R[0-9A-HJKMNP-TV-Z]{8}$')
);

COMMENT ON COLUMN public.company_roles.is_system IS
  'True for seeded templates. Only SUPER_ADMIN may create or modify non-system roles initially.';

CREATE INDEX IF NOT EXISTS ix_company_roles_company ON public.company_roles (company_id) WHERE deleted_at IS NULL;

CREATE OR REPLACE FUNCTION app.assign_role_public_id() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_try int := 0; v_id text;
BEGIN

  LOOP
    v_id := app.gen_public_id('R', 8);
    EXIT WHEN NOT EXISTS (SELECT 1 FROM public.company_roles WHERE public_id = v_id);
    v_try := v_try + 1;
    IF v_try > 5 THEN RAISE EXCEPTION 'could not allocate role public_id'; END IF;
  END LOOP;
  NEW.public_id := v_id;
  RETURN NEW;
END
$$;

SELECT app.attach_triggers('public.company_roles'::regclass);
DROP TRIGGER IF EXISTS trg_company_roles_public_id ON public.company_roles;
CREATE TRIGGER trg_company_roles_public_id BEFORE INSERT ON public.company_roles
  FOR EACH ROW WHEN (NEW.public_id IS NULL)
  EXECUTE FUNCTION app.assign_role_public_id();

-- -----------------------------------------------------------------------------
-- role_permissions — the join. A row grants exactly one permission key.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.role_permissions (
  role_id      uuid NOT NULL REFERENCES public.company_roles(id) ON DELETE CASCADE,
  permission_key text NOT NULL REFERENCES public.permissions(key) ON DELETE CASCADE,
  granted_at   timestamptz NOT NULL DEFAULT now(),
  granted_by   uuid REFERENCES public.users(id) ON DELETE SET NULL,
  PRIMARY KEY (role_id, permission_key)
);

CREATE INDEX IF NOT EXISTS ix_role_perm_lookup ON public.role_permissions (permission_key, role_id);

-- -----------------------------------------------------------------------------
-- company_memberships — the tenant edge. Status is enforced in RLS: only ACTIVE
-- memberships grant any company-scoped access.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.company_memberships (
  id           uuid PRIMARY KEY DEFAULT app.uuid7(),
  company_id   uuid NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
  user_id      uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  -- CASCADE, not RESTRICT: deleting a company cascades to its roles, and a
  -- membership cannot meaningfully outlive the role it grants. RESTRICT is
  -- evaluated eagerly inside the cascade, so it would make company deletion
  -- impossible. Role *renaming* and permission changes are what the API guards.
  role_id      uuid NOT NULL REFERENCES public.company_roles(id) ON DELETE CASCADE,
  status       public.membership_status NOT NULL DEFAULT 'ACTIVE',
  job_title    text,
  department   text,
  employment_type text CHECK (employment_type IS NULL
                              OR employment_type IN ('FULL_TIME','PART_TIME','CONTRACT','CONSULTANT','INTERN')),
  hourly_rate  numeric(18,4),          -- internal cost, restricted to finance permissions
  currency     char(3) NOT NULL DEFAULT 'USD',
  start_date   date,
  end_date     date,
  invited_by   uuid REFERENCES public.users(id) ON DELETE SET NULL,
  invited_at   timestamptz NOT NULL DEFAULT now(),
  joined_at    timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (company_id, user_id),
  CONSTRAINT ck_membership_dates CHECK (end_date IS NULL OR start_date IS NULL OR end_date >= start_date)
);

COMMENT ON TABLE public.company_memberships IS
  'Company membership. Grants company *context* only — every action still requires a permission.';
COMMENT ON COLUMN public.company_memberships.hourly_rate IS
  'Internal billing rate. Readable only with timesheets.read_rate or finance permissions; never in social APIs.';

CREATE INDEX IF NOT EXISTS ix_membership_user   ON public.company_memberships (user_id, status);
CREATE INDEX IF NOT EXISTS ix_membership_company ON public.company_memberships (company_id, status);
CREATE INDEX IF NOT EXISTS ix_membership_role   ON public.company_memberships (role_id) WHERE status = 'ACTIVE';

-- -----------------------------------------------------------------------------
-- company_invitations — invite a registered user or an email that will sign up
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.company_invitations (
  id             uuid PRIMARY KEY DEFAULT app.uuid7(),
  company_id     uuid NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
  email          citext NOT NULL,
  role_id        uuid NOT NULL REFERENCES public.company_roles(id) ON DELETE CASCADE,
  invited_by     uuid NOT NULL REFERENCES public.users(id) ON DELETE RESTRICT,
  token_hash     text NOT NULL UNIQUE,        -- sha256 of the single-use token; never the token
  status         text NOT NULL DEFAULT 'PENDING'
                   CHECK (status IN ('PENDING','ACCEPTED','REVOKED','EXPIRED')),
  job_title      text,
  message        text,
  expires_at     timestamptz NOT NULL DEFAULT (now() + interval '7 days'),
  accepted_at    timestamptz,
  accepted_user_id uuid REFERENCES public.users(id) ON DELETE SET NULL,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (company_id, email, status)
);

CREATE INDEX IF NOT EXISTS ix_invitations_email ON public.company_invitations (email);
CREATE INDEX IF NOT EXISTS ix_invitations_pending
  ON public.company_invitations (company_id, created_at DESC) WHERE status = 'PENDING';

-- -----------------------------------------------------------------------------
-- company_settings_history — policy changes are auditable over time
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.company_settings_history (
  id          uuid PRIMARY KEY DEFAULT app.uuid7(),
  company_id  uuid NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
  changed_by  uuid REFERENCES public.users(id) ON DELETE SET NULL,
  old_settings jsonb,
  new_settings jsonb NOT NULL,
  changed_fields text[],
  reason      text,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_settings_history_company
  ON public.company_settings_history (company_id, created_at DESC);

-- =============================================================================
-- Authorization decision functions
-- These are the ONLY place authorization is decided in SQL. SECURITY DEFINER
-- with a fixed search_path so callers need no privileges on the base tables.
-- =============================================================================

-- Active membership? Returns the membership row id or NULL.
CREATE OR REPLACE FUNCTION app.active_membership(
  p_user_id uuid DEFAULT app.current_user_id(),
  p_company_id uuid DEFAULT app.current_company_id()
) RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, app, pg_temp
AS $$
  SELECT m.id
    FROM public.company_memberships m
   WHERE m.user_id = p_user_id
     AND m.company_id = p_company_id
     AND m.status = 'ACTIVE'
   LIMIT 1;
$$;

-- Is the current user an active member of the given company?
CREATE OR REPLACE FUNCTION app.is_member(
  p_company_id uuid,
  p_user_id uuid DEFAULT app.current_user_id()
) RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT app.active_membership(p_user_id, p_company_id) IS NOT NULL;
$$;

-- Does the current user hold a specific permission in a company?
-- SUPER_ADMIN expands '*' but still resolves through the join, so revoking a
-- key from the SUPER_ADMIN role genuinely revokes it.
CREATE OR REPLACE FUNCTION app.has_permission(
  p_company_id uuid,
  p_permission text,
  p_user_id uuid DEFAULT app.current_user_id()
) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, app, pg_temp
AS $$
  SELECT EXISTS (
    SELECT 1
      FROM public.company_memberships m
      JOIN public.company_roles r
        ON r.id = m.role_id AND r.deleted_at IS NULL
      JOIN public.role_permissions rp
        ON rp.role_id = r.id
      JOIN public.permissions p
        ON p.key = rp.permission_key
     WHERE m.user_id = p_user_id
       AND m.company_id = p_company_id
       AND m.status = 'ACTIVE'
       AND (
         p.key = p_permission
         OR p.key LIKE p_permission || '.%'      -- implied by a module-scoped grant
         OR rp.permission_key = '*'
       )
  );
$$;

COMMENT ON FUNCTION app.has_permission(uuid, text, uuid) IS
  'Single authorization decision point used by RLS and by FastAPI require_permission().';

-- Permission check for any one of several keys (convenience for RLS policies).
CREATE OR REPLACE FUNCTION app.has_any_permission(
  p_company_id uuid,
  p_permissions text[],
  p_user_id uuid DEFAULT app.current_user_id()
) RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT COALESCE(bool_or(app.has_permission(p_company_id, k, p_user_id)), false)
    FROM unnest(p_permissions) AS k;
$$;

-- Effective role keys for the current user in a company (used by the UI to
-- render navigation, and by tests to assert least privilege).
CREATE OR REPLACE FUNCTION app.effective_role_keys(
  p_company_id uuid,
  p_user_id uuid DEFAULT app.current_user_id()
) RETURNS text[]
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, app, pg_temp
AS $$
  SELECT COALESCE(array_agg(DISTINCT r.key), '{}')
    FROM public.company_memberships m
    JOIN public.company_roles r ON r.id = m.role_id AND r.deleted_at IS NULL
   WHERE m.user_id = p_user_id AND m.company_id = p_company_id AND m.status = 'ACTIVE';
$$;

-- All permission keys the current user holds in the active company.
CREATE OR REPLACE FUNCTION app.my_permissions(
  p_company_id uuid DEFAULT app.current_company_id(),
  p_user_id uuid DEFAULT app.current_user_id()
) RETURNS SETOF text
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, app, pg_temp
AS $$
  SELECT DISTINCT p.key
    FROM public.company_memberships m
    JOIN public.company_roles r ON r.id = m.role_id AND r.deleted_at IS NULL
    JOIN public.role_permissions rp ON rp.role_id = r.id
    JOIN public.permissions p
      ON p.key = rp.permission_key OR p.key LIKE rp.permission_key || '.%'
   WHERE m.user_id = p_user_id AND m.company_id = p_company_id AND m.status = 'ACTIVE';
$$;

-- Guard: a company must always retain at least one SUPER_ADMIN.
CREATE OR REPLACE FUNCTION app.assert_last_super_admin() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  v_company uuid := COALESCE(OLD.company_id, NEW.company_id);
  v_role_is_super boolean;
  v_remaining int;
BEGIN
  -- Migrations, seeds and background workers have no end-user identity.
  -- mytrakin_api is neither superuser nor BYPASSRLS, so request traffic
  -- always falls through to the real checks below.
  IF app.is_trusted_context() THEN
    RETURN NEW;
  END IF;

  IF (TG_OP = 'DELETE' OR (TG_OP = 'UPDATE' AND (NEW.role_id <> OLD.role_id OR NEW.status <> OLD.status)))
     AND OLD.status = 'ACTIVE' THEN
    SELECT r.is_system AND r.key = 'SUPER_ADMIN' INTO v_role_is_super
      FROM public.company_roles r WHERE r.id = OLD.role_id;

    IF v_role_is_super THEN
      SELECT count(*) INTO v_remaining
        FROM public.company_memberships m
        JOIN public.company_roles r ON r.id = m.role_id
       WHERE m.company_id = v_company
         AND m.status = 'ACTIVE'
         AND r.key = 'SUPER_ADMIN'
         AND m.id <> OLD.id;

      IF v_remaining = 0 THEN
        RAISE EXCEPTION 'company % must retain at least one active SUPER_ADMIN', v_company
          USING ERRCODE = 'restrict_violation';
      END IF;
    END IF;
  END IF;
  RETURN COALESCE(NEW, OLD);
END
$$;

DROP TRIGGER IF EXISTS trg_membership_last_admin ON public.company_memberships;
CREATE TRIGGER trg_membership_last_admin
  BEFORE UPDATE OR DELETE ON public.company_memberships
  FOR EACH ROW EXECUTE FUNCTION app.assert_last_super_admin();

-- Role permission changes are audited automatically.
CREATE OR REPLACE FUNCTION app.audit_role_permission_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, app, platform, pg_temp
AS $$
DECLARE
  v_company uuid;
  v_action text;
BEGIN

  SELECT company_id INTO v_company FROM public.company_roles WHERE id = COALESCE(NEW.role_id, OLD.role_id);

  IF TG_OP = 'INSERT' THEN v_action := 'role.permission.granted';
  ELSIF TG_OP = 'DELETE' THEN v_action := 'role.permission.revoked';
  ELSE v_action := 'role.permission.updated'; END IF;

  INSERT INTO platform.audit_logs (
    company_id, actor_user_id, actor_type, action,
    resource_type, resource_id, old_values, new_values,
    request_id, metadata
  ) VALUES (
    v_company, app.current_user_id(), 'USER', v_action,
    'company_role', COALESCE(NEW.role_id, OLD.role_id),
    CASE WHEN TG_OP = 'INSERT' THEN NULL
         ELSE jsonb_build_object('permission_key', OLD.permission_key) END,
    CASE WHEN TG_OP = 'DELETE' THEN NULL
         ELSE jsonb_build_object('permission_key', NEW.permission_key) END,
    app.current_request_id(),
    jsonb_build_object('module', 'rbac')
  );
  RETURN COALESCE(NEW, OLD);
END
$$;

DROP TRIGGER IF EXISTS trg_role_perm_audit ON public.role_permissions;
CREATE TRIGGER trg_role_perm_audit
  AFTER INSERT OR UPDATE OR DELETE ON public.role_permissions
  FOR EACH ROW EXECUTE FUNCTION app.audit_role_permission_change();

-- Membership changes are audited automatically (including self-approval defence).
CREATE OR REPLACE FUNCTION app.audit_membership_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, app, platform, pg_temp
AS $$
DECLARE
  v_action text;
  v_actor public.actor_type;
  v_actor_user uuid;
BEGIN

  -- System-initiated membership creation (company founding) has no session user.
  IF app.current_user_id() IS NULL THEN
    v_actor := 'SYSTEM';
    v_actor_user := NULL;
  ELSE
    v_actor := 'USER';
    v_actor_user := app.current_user_id();
  END IF;

  v_action := CASE TG_OP
    WHEN 'INSERT' THEN 'membership.created'
    WHEN 'DELETE' THEN 'membership.removed'
    WHEN 'UPDATE' THEN 'membership.updated'
  END;

  INSERT INTO platform.audit_logs (
    company_id, actor_user_id, actor_type, action,
    resource_type, resource_id, old_values, new_values, request_id
  ) VALUES (
    COALESCE(NEW.company_id, OLD.company_id), v_actor_user, v_actor, v_action,
    'company_membership', COALESCE(NEW.id, OLD.id),
    CASE WHEN TG_OP = 'INSERT' THEN NULL
         ELSE to_jsonb(OLD) - 'id' - 'company_id' - 'user_id' END,
    CASE WHEN TG_OP = 'DELETE' THEN NULL
         ELSE to_jsonb(NEW) - 'id' - 'company_id' - 'user_id' END,
    app.current_request_id()
  );
  RETURN COALESCE(NEW, OLD);
END
$$;

DROP TRIGGER IF EXISTS trg_membership_audit ON public.company_memberships;
CREATE TRIGGER trg_membership_audit
  AFTER INSERT OR UPDATE OR DELETE ON public.company_memberships
  FOR EACH ROW EXECUTE FUNCTION app.audit_membership_change();

GRANT EXECUTE ON FUNCTION app.has_permission(uuid, text, uuid) TO mytrakin_api, mytrakin_worker, mytrakin_readonly;
GRANT EXECUTE ON FUNCTION app.has_any_permission(uuid, text[], uuid) TO mytrakin_api, mytrakin_worker;
GRANT EXECUTE ON FUNCTION app.effective_role_keys(uuid, uuid) TO mytrakin_api, mytrakin_worker;
GRANT EXECUTE ON FUNCTION app.my_permissions(uuid, uuid) TO mytrakin_api;
GRANT EXECUTE ON FUNCTION app.is_member(uuid, uuid) TO mytrakin_api, mytrakin_worker;
GRANT EXECUTE ON FUNCTION app.active_membership(uuid, uuid) TO mytrakin_api, mytrakin_worker;

GRANT SELECT, INSERT, UPDATE, DELETE ON
  public.companies, public.permissions, public.company_roles,
  public.role_permissions, public.company_memberships,
  public.company_invitations, public.company_settings_history
TO mytrakin_api, mytrakin_worker;
GRANT SELECT ON public.permissions TO mytrakin_readonly;
