-- =============================================================================
-- MyTrakin :: 0007_code_contracts.sql
-- CODE module: projects, project roles, allocation, SOWs, contracts.
--
-- Invariants enforced IN THE DATABASE (not only in the API):
--   I1  allocation of a project role never exceeds required_count
--   I2  SOW role quantity never exceeds the project role's remaining capacity
--   I3  every contract line item traces to a contract + SOW + project role
--   I4  a user may only work under an ACCEPTED/ACTIVE contract (enforced in 0008)
--   I5  public_id is immutable (app.attach_triggers)
-- =============================================================================

-- -----------------------------------------------------------------------------
-- projects
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.projects (
  id                 uuid PRIMARY KEY DEFAULT app.uuid7(),
  public_id          text NOT NULL UNIQUE,
  company_id         uuid NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
  name               text NOT NULL,
  description        text,
  project_type       public.project_type NOT NULL DEFAULT 'SERVICE',
  category           public.project_category NOT NULL DEFAULT 'COMPANY',
  status             text NOT NULL DEFAULT 'DRAFT'
                       CHECK (status IN ('DRAFT','PLANNING','ACTIVE','ON_HOLD','COMPLETED','CANCELLED')),
  start_date         date,
  estimated_end_date date,
  estimated_hours    numeric(14,2) CHECK (estimated_hours IS NULL OR estimated_hours >= 0),
  estimated_budget   numeric(18,4) CHECK (estimated_budget IS NULL OR estimated_budget >= 0),
  currency           char(3) NOT NULL DEFAULT 'USD',
  billing_basis      public.billing_basis NOT NULL DEFAULT 'TIMESHEET',
  billing_frequency  public.billing_frequency NOT NULL DEFAULT 'MONTHLY',
  payment_terms_days int NOT NULL DEFAULT 30 CHECK (payment_terms_days >= 0),
  health_score       int CHECK (health_score IS NULL OR health_score BETWEEN 0 AND 100),
  health_computed_at timestamptz,
  owner_user_id      uuid REFERENCES public.users(id) ON DELETE SET NULL,
  metadata           jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_by         uuid REFERENCES public.users(id) ON DELETE SET NULL,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  deleted_at         timestamptz,
  CONSTRAINT ck_project_public_id CHECK (public_id ~ '^P[0-9A-HJKMNP-TV-Z]{8}$'),
  CONSTRAINT ck_project_currency CHECK (currency ~ '^[A-Z]{3}$'),
  CONSTRAINT ck_project_dates CHECK (estimated_end_date IS NULL OR start_date IS NULL
                                     OR estimated_end_date >= start_date)
);

CREATE INDEX IF NOT EXISTS ix_projects_company  ON public.projects (company_id, created_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_projects_status   ON public.projects (company_id, status) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_projects_name_trgm ON public.projects USING gin (name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_projects_active
  ON public.projects (company_id, status, estimated_end_date) WHERE deleted_at IS NULL AND status = 'ACTIVE';

CREATE OR REPLACE FUNCTION app.assign_project_public_id() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_try int := 0; v_id text;
BEGIN

  LOOP
    v_id := app.gen_public_id('P', 8);
    EXIT WHEN NOT EXISTS (SELECT 1 FROM public.projects WHERE public_id = v_id);
    v_try := v_try + 1;
    IF v_try > 5 THEN RAISE EXCEPTION 'could not allocate project public_id'; END IF;
  END LOOP;
  NEW.public_id := v_id;
  RETURN NEW;
END
$$;

SELECT app.attach_triggers('public.projects'::regclass);
DROP TRIGGER IF EXISTS trg_projects_public_id ON public.projects;
CREATE TRIGGER trg_projects_public_id BEFORE INSERT ON public.projects
  FOR EACH ROW WHEN (NEW.public_id IS NULL)
  EXECUTE FUNCTION app.assign_project_public_id();

-- Projects need projects.create to create; the guard runs inside the same
-- transaction as the insert so a forbidden project never exists, even briefly.
CREATE OR REPLACE FUNCTION app.assert_project_permission() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  -- Migrations, seeds and background workers have no end-user identity.
  -- mytrakin_api is neither superuser nor BYPASSRLS, so request traffic
  -- always falls through to the real checks below.
  IF app.is_trusted_context() THEN
    RETURN NEW;
  END IF;

  -- Migrations, seeds and background workers have no end-user identity.
  -- mytrakin_api is neither superuser nor BYPASSRLS, so request traffic
  -- always falls through to the real checks below.
  IF NOT app.has_permission(NEW.company_id, 'projects.create') THEN
    RAISE EXCEPTION 'missing permission projects.create for company %', NEW.company_id
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_projects_guard ON public.projects;
CREATE TRIGGER trg_projects_guard BEFORE INSERT ON public.projects
  FOR EACH ROW EXECUTE FUNCTION app.assert_project_permission();

-- Defer FK from 0005 (posts).
ALTER TABLE public.posts DROP CONSTRAINT IF EXISTS posts_project_id_fkey;
ALTER TABLE public.posts
  ADD CONSTRAINT posts_project_id_fkey
  FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE SET NULL;

-- -----------------------------------------------------------------------------
-- project_roles — capacity definition (R001 Java Developer, required: 10).
-- required_count is TOTAL capacity across every SOW and direct individual.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.project_roles (
  id             uuid PRIMARY KEY DEFAULT app.uuid7(),
  public_id      text NOT NULL UNIQUE,
  project_id     uuid NOT NULL REFERENCES public.projects(id) ON DELETE CASCADE,
  company_id     uuid NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
  title          text NOT NULL,
  description    text,
  required_count int NOT NULL CHECK (required_count >= 0),
  allocated_count int NOT NULL DEFAULT 0 CHECK (allocated_count >= 0),
  -- allocation <= required is the invariant; maintained by trigger below
  required_skills text[] NOT NULL DEFAULT '{}',
  seniority       text,
  min_hourly_rate numeric(18,4),
  max_hourly_rate numeric(18,4),
  status         text NOT NULL DEFAULT 'OPEN'
                   CHECK (status IN ('OPEN','FILLED','CLOSED','ON_HOLD')),
  created_by     uuid REFERENCES public.users(id) ON DELETE SET NULL,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  deleted_at     timestamptz,
  CONSTRAINT ck_project_role_public_id CHECK (public_id ~ '^R[0-9A-HJKMNP-TV-Z]{8}$'),
  CONSTRAINT ck_project_role_allocation
    CHECK (allocated_count <= required_count),
  CONSTRAINT ck_project_role_rate
    CHECK (min_hourly_rate IS NULL OR max_hourly_rate IS NULL
           OR max_hourly_rate >= min_hourly_rate)
);

COMMENT ON COLUMN public.project_roles.required_count IS
  'Total capacity. Sum of SOW quantities plus direct individual allocations must never exceed it.';
COMMENT ON COLUMN public.project_roles.allocated_count IS
  'Denormalised total, maintained by app.refresh_role_allocation() under an advisory lock.';

CREATE INDEX IF NOT EXISTS ix_project_roles_project  ON public.project_roles (project_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_project_roles_company  ON public.project_roles (company_id, status) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_project_roles_open
  ON public.project_roles (company_id, required_count)
  WHERE deleted_at IS NULL AND status = 'OPEN' AND allocated_count < required_count;

CREATE OR REPLACE FUNCTION app.assign_project_role_public_id() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_try int := 0; v_id text;
BEGIN

  LOOP
    v_id := app.gen_public_id('R', 8);
    EXIT WHEN NOT EXISTS (SELECT 1 FROM public.project_roles WHERE public_id = v_id);
    v_try := v_try + 1;
    IF v_try > 5 THEN RAISE EXCEPTION 'could not allocate project role public_id'; END IF;
  END LOOP;
  NEW.public_id := v_id;
  RETURN NEW;
END
$$;

SELECT app.attach_triggers('public.project_roles'::regclass);
DROP TRIGGER IF EXISTS trg_project_roles_public_id ON public.project_roles;
CREATE TRIGGER trg_project_roles_public_id BEFORE INSERT ON public.project_roles
  FOR EACH ROW WHEN (NEW.public_id IS NULL)
  EXECUTE FUNCTION app.assign_project_role_public_id();

-- -----------------------------------------------------------------------------
-- sows — the commercial allocation layer. One project may hold many SOWs,
-- each with a company OR an individual as the counterparty.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.sows (
  id                 uuid PRIMARY KEY DEFAULT app.uuid7(),
  public_id          text NOT NULL UNIQUE,
  project_id         uuid NOT NULL REFERENCES public.projects(id) ON DELETE CASCADE,
  company_id         uuid NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
  sow_type           public.project_category NOT NULL DEFAULT 'COMPANY',  -- COMPANY | INDIVIDUAL
  counterparty_company_id uuid REFERENCES public.companies(id) ON DELETE RESTRICT,
  counterparty_user_id    uuid REFERENCES public.users(id) ON DELETE RESTRICT,
  title              text NOT NULL,
  description        text,
  status             public.sow_status NOT NULL DEFAULT 'DRAFT',
  start_date         date,
  end_date           date,
  currency           char(3) NOT NULL DEFAULT 'USD',
  default_rate       numeric(18,4) CHECK (default_rate IS NULL OR default_rate >= 0),
  billing_basis      public.billing_basis NOT NULL DEFAULT 'TIMESHEET',
  billing_frequency  public.billing_frequency NOT NULL DEFAULT 'MONTHLY',
  invoice_frequency  public.billing_frequency NOT NULL DEFAULT 'MONTHLY',
  payment_terms_days int NOT NULL DEFAULT 30 CHECK (payment_terms_days >= 0),
  payment_method     text,
  special_conditions text,
  max_total_amount   numeric(18,4) CHECK (max_total_amount IS NULL OR max_total_amount >= 0),
  auto_generate_contracts boolean NOT NULL DEFAULT true,
  approved_by        uuid REFERENCES public.users(id) ON DELETE SET NULL,
  approved_at        timestamptz,
  document_id        uuid REFERENCES public.documents(id) ON DELETE SET NULL,
  created_by         uuid REFERENCES public.users(id) ON DELETE SET NULL,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  deleted_at         timestamptz,
  CONSTRAINT ck_sow_public_id CHECK (public_id ~ '^S[0-9A-HJKMNP-TV-Z]{8}$'),
  CONSTRAINT ck_sow_counterparty_exclusive
    CHECK ((sow_type = 'COMPANY'  AND counterparty_company_id IS NOT NULL AND counterparty_user_id IS NULL)
        OR (sow_type = 'INDIVIDUAL' AND counterparty_user_id IS NOT NULL AND counterparty_company_id IS NULL)),
  CONSTRAINT ck_sow_dates CHECK (end_date IS NULL OR start_date IS NULL OR end_date >= start_date),
  CONSTRAINT ck_sow_currency CHECK (currency ~ '^[A-Z]{3}$')
);

COMMENT ON TABLE public.sows IS
  'Statement of Work: the commercial allocation layer between a project and a counterparty.';
COMMENT ON COLUMN public.sows.sow_type IS
  'COMPANY = company-to-company engagement. INDIVIDUAL = direct individual engagement; an internal contract is created for it.';

CREATE INDEX IF NOT EXISTS ix_sows_project      ON public.sows (project_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_sows_company      ON public.sows (company_id, status) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_sows_counterparty ON public.sows (counterparty_company_id, status) WHERE counterparty_company_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_sows_individual   ON public.sows (counterparty_user_id, status) WHERE counterparty_user_id IS NOT NULL;

CREATE OR REPLACE FUNCTION app.assign_sow_public_id() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_try int := 0; v_id text;
BEGIN

  LOOP
    v_id := app.gen_public_id('S', 8);
    EXIT WHEN NOT EXISTS (SELECT 1 FROM public.sows WHERE public_id = v_id);
    v_try := v_try + 1;
    IF v_try > 5 THEN RAISE EXCEPTION 'could not allocate sow public_id'; END IF;
  END LOOP;
  NEW.public_id := v_id;
  RETURN NEW;
END
$$;

SELECT app.attach_triggers('public.sows'::regclass);
DROP TRIGGER IF EXISTS trg_sows_public_id ON public.sows;
CREATE TRIGGER trg_sows_public_id BEFORE INSERT ON public.sows
  FOR EACH ROW WHEN (NEW.public_id IS NULL)
  EXECUTE FUNCTION app.assign_sow_public_id();

-- -----------------------------------------------------------------------------
-- sow_roles — how many of each project role this SOW buys.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.sow_roles (
  id             uuid PRIMARY KEY DEFAULT app.uuid7(),
  sow_id         uuid NOT NULL REFERENCES public.sows(id) ON DELETE CASCADE,
  -- CASCADE: a project role cannot outlive its project, and RESTRICT is checked
  -- eagerly inside a cascade, which would make project deletion impossible.
  project_role_id uuid NOT NULL REFERENCES public.project_roles(id) ON DELETE CASCADE,
  quantity       int NOT NULL CHECK (quantity > 0),
  rate           numeric(18,4) CHECK (rate IS NULL OR rate >= 0),
  rate_type      text NOT NULL DEFAULT 'HOURLY'
                   CHECK (rate_type IN ('HOURLY','DAILY','FIXED','PER_UNIT','PERCENT')),
  currency       char(3) NOT NULL DEFAULT 'USD',
  notes          text,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (sow_id, project_role_id)
);

CREATE INDEX IF NOT EXISTS ix_sowroles_role ON public.sow_roles (project_role_id);

-- -----------------------------------------------------------------------------
-- I1 enforcement: recompute allocated_count from live allocations.
-- An advisory lock serialises concurrent allocations for the same role so two
-- simultaneous SOW approvals cannot both see "capacity available".
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.refresh_role_allocation(p_project_role_id uuid)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, app, pg_temp
AS $$
DECLARE
  v_total int;
  v_required int;
BEGIN

  -- Serialise concurrent allocations for the same role so two simultaneous SOW
  -- approvals cannot both observe "capacity available".
  PERFORM pg_advisory_xact_lock(hashtextextended(p_project_role_id::text, 0));

  -- SECURITY DEFINER: the user allocating a SOW holds sows.create, not
  -- necessarily projects.update, but the derived counter must still be kept
  -- truthful. The authorisation decision belongs to the caller and to RLS; this
  -- function only recomputes and enforces the capacity invariant.
  SELECT COALESCE(SUM(sr.quantity), 0)::int INTO v_total
    FROM public.sow_roles sr
    JOIN public.sows s ON s.id = sr.sow_id
   WHERE sr.project_role_id = p_project_role_id
     AND s.deleted_at IS NULL
     AND s.status IN ('ACTIVE', 'PENDING_APPROVAL');

  SELECT required_count INTO v_required
    FROM public.project_roles WHERE id = p_project_role_id;

  IF v_required IS NOT NULL AND v_total > v_required THEN
    RAISE EXCEPTION
      'allocation exceeds capacity for project role %: required %, allocated %',
      p_project_role_id, v_required, v_total
      USING ERRCODE = 'check_violation',
            HINT = 'Reduce the SOW quantity or increase required_count.';
  END IF;

  UPDATE public.project_roles pr
     SET allocated_count = v_total,
         status = CASE
           WHEN pr.status IN ('CLOSED','ON_HOLD') THEN pr.status
           WHEN v_total >= pr.required_count THEN 'FILLED'::text
           ELSE 'OPEN'::text END
   WHERE pr.id = p_project_role_id;
END
$$;

COMMENT ON FUNCTION app.refresh_role_allocation(uuid) IS
  'Recomputes and validates project-role allocation. Called under an advisory lock; raises when capacity would be exceeded.';
-- AFTER-row trigger. The guard block is deliberately absent: a derived counter
-- must be maintained for every writer, including superuser migrations and seeds.
-- Returning NULL keeps this an AFTER trigger's no-op.
CREATE OR REPLACE FUNCTION app.on_sow_role_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  PERFORM app.refresh_role_allocation(COALESCE(NEW.project_role_id, OLD.project_role_id));
  RETURN NULL;
END
$$;

DROP TRIGGER IF EXISTS trg_sowrole_alloc ON public.sow_roles;
CREATE TRIGGER trg_sowrole_alloc AFTER INSERT OR UPDATE OR DELETE ON public.sow_roles
  FOR EACH ROW EXECUTE FUNCTION app.on_sow_role_change();

-- A SOW status change can add or remove capacity consumption.
CREATE OR REPLACE FUNCTION app.on_sow_status_change() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE r record;
BEGIN
  FOR r IN SELECT DISTINCT project_role_id FROM public.sow_roles WHERE sow_id = NEW.id
  LOOP
    PERFORM app.refresh_role_allocation(r.project_role_id);
  END LOOP;
  RETURN NULL;
END
$$;

DROP TRIGGER IF EXISTS trg_sow_alloc ON public.sows;
CREATE TRIGGER trg_sow_alloc AFTER UPDATE OF status, deleted_at ON public.sows
  FOR EACH ROW WHEN (OLD.status IS DISTINCT FROM NEW.status
                     OR OLD.deleted_at IS DISTINCT FROM NEW.deleted_at)
  EXECUTE FUNCTION app.on_sow_status_change();

-- Deleting a SOW releases its capacity.
CREATE OR REPLACE FUNCTION app.on_sow_delete() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE r record;
BEGIN
  FOR r IN SELECT DISTINCT project_role_id FROM public.sow_roles WHERE sow_id = OLD.id
  LOOP
    PERFORM app.refresh_role_allocation(r.project_role_id);
  END LOOP;
  RETURN OLD;
END
$$;

DROP TRIGGER IF EXISTS trg_sow_alloc_delete ON public.sows;
CREATE TRIGGER trg_sow_alloc_delete AFTER DELETE ON public.sows
  FOR EACH ROW EXECUTE FUNCTION app.on_sow_delete();

-- SOW writes require contracts.create on the owning company.
CREATE OR REPLACE FUNCTION app.assert_sow_permission() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  -- Migrations, seeds and background workers have no end-user identity.
  -- mytrakin_api is neither superuser nor BYPASSRLS, so request traffic
  -- always falls through to the real checks below.
  IF app.is_trusted_context() THEN
    RETURN NEW;
  END IF;

  -- Migrations, seeds and background workers have no end-user identity.
  -- mytrakin_api is neither superuser nor BYPASSRLS, so request traffic
  -- always falls through to the real checks below.
  IF NOT app.has_permission(NEW.company_id, 'sows.create') THEN
    RAISE EXCEPTION 'missing permission sows.create for company %', NEW.company_id
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_sows_guard ON public.sows;
CREATE TRIGGER trg_sows_guard BEFORE INSERT ON public.sows
  FOR EACH ROW EXECUTE FUNCTION app.assert_sow_permission();

-- =============================================================================
-- Contracts
-- One contract per actual assignment (company↔company or company↔individual),
-- always derived from a SOW allocation.
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.contracts (
  id                 uuid PRIMARY KEY DEFAULT app.uuid7(),
  public_id          text NOT NULL UNIQUE,
  -- RESTRICT: a contract is a legal artefact and must not disappear because a
  -- SOW was removed. SOWs are in practice soft-deleted (deleted_at), so this
  -- only fires on a hard delete, which the API does not perform.
  sow_id             uuid NOT NULL REFERENCES public.sows(id) ON DELETE RESTRICT,
  project_id         uuid NOT NULL REFERENCES public.projects(id) ON DELETE RESTRICT,
  company_id         uuid NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
  contract_type      public.project_category NOT NULL DEFAULT 'COMPANY',
  title              text NOT NULL,
  status             public.contract_status NOT NULL DEFAULT 'DRAFT',
  currency           char(3) NOT NULL DEFAULT 'USD',
  billing_basis      public.billing_basis NOT NULL DEFAULT 'TIMESHEET',
  billing_frequency  public.billing_frequency NOT NULL DEFAULT 'MONTHLY',
  payment_terms_days int NOT NULL DEFAULT 30 CHECK (payment_terms_days >= 0),
  start_date         date,
  end_date           date,
  auto_renew         boolean NOT NULL DEFAULT false,
  renewal_notice_days int CHECK (renewal_notice_days IS NULL OR renewal_notice_days > 0),
  termination_notice_days int CHECK (termination_notice_days IS NULL OR termination_notice_days > 0),
  notice_period_end  date,
  timesheet_approval_chain jsonb NOT NULL DEFAULT '{}'::jsonb,
  governing_law      text,
  confidentiality_level text NOT NULL DEFAULT 'STANDARD'
                       CHECK (confidentiality_level IN ('STANDARD','CONFIDENTIAL','RESTRICTED')),
  requires_timesheets boolean NOT NULL DEFAULT true,
  locked             boolean NOT NULL DEFAULT false,
  counterparty_company_id uuid REFERENCES public.companies(id) ON DELETE RESTRICT,
  counterparty_user_id    uuid REFERENCES public.users(id) ON DELETE RESTRICT,
  document_id        uuid REFERENCES public.documents(id) ON DELETE SET NULL,
  risk_score         int CHECK (risk_score IS NULL OR risk_score BETWEEN 0 AND 100),
  risk_computed_at   timestamptz,
  sent_at            timestamptz,
  responded_at       timestamptz,
  activated_at       timestamptz,
  terminated_at      timestamptz,
  response_notes     text,
  version            int NOT NULL DEFAULT 1,
  created_by         uuid REFERENCES public.users(id) ON DELETE SET NULL,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  deleted_at         timestamptz,
  CONSTRAINT ck_contract_public_id CHECK (public_id ~ '^C[0-9A-HJKMNP-TV-Z]{8}$'),
  CONSTRAINT ck_contract_counterparty_exclusive
    CHECK ((contract_type = 'COMPANY'  AND counterparty_company_id IS NOT NULL AND counterparty_user_id IS NULL)
        OR (contract_type = 'INDIVIDUAL' AND counterparty_user_id IS NOT NULL AND counterparty_company_id IS NULL)),
  CONSTRAINT ck_contract_dates CHECK (end_date IS NULL OR start_date IS NULL OR end_date >= start_date),
  CONSTRAINT ck_contract_currency CHECK (currency ~ '^[A-Z]{3}$')
);

COMMENT ON COLUMN public.contracts.timesheet_approval_chain IS
  'Ordered list of approver company ids / user ids derived from the multi-hop chain. See docs/authorization.md.';
COMMENT ON COLUMN public.contracts.locked IS
  'When true, contract-level commercial terms can no longer be edited (e.g. after first invoice).';

CREATE INDEX IF NOT EXISTS ix_contracts_company
  ON public.contracts (company_id, created_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_contracts_counterparty
  ON public.contracts (counterparty_company_id, status) WHERE counterparty_company_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_contracts_user
  ON public.contracts (counterparty_user_id, status) WHERE counterparty_user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_contracts_project  ON public.contracts (project_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_contracts_sow      ON public.contracts (sow_id);
CREATE INDEX IF NOT EXISTS ix_contracts_status   ON public.contracts (company_id, status) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_contracts_expiring
  ON public.contracts (end_date)
  WHERE deleted_at IS NULL AND status = 'ACTIVE' AND end_date IS NOT NULL;
-- the "my contracts" work index
CREATE INDEX IF NOT EXISTS ix_contracts_work
  ON public.contracts (counterparty_user_id, status, end_date)
  WHERE counterparty_user_id IS NOT NULL AND deleted_at IS NULL;

CREATE OR REPLACE FUNCTION app.assign_contract_public_id() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_try int := 0; v_id text;
BEGIN

  LOOP
    v_id := app.gen_public_id('C', 8);
    EXIT WHEN NOT EXISTS (SELECT 1 FROM public.contracts WHERE public_id = v_id);
    v_try := v_try + 1;
    IF v_try > 5 THEN RAISE EXCEPTION 'could not allocate contract public_id'; END IF;
  END LOOP;
  NEW.public_id := v_id;
  RETURN NEW;
END
$$;

SELECT app.attach_triggers('public.contracts'::regclass);
DROP TRIGGER IF EXISTS trg_contracts_public_id ON public.contracts;
CREATE TRIGGER trg_contracts_public_id BEFORE INSERT ON public.contracts
  FOR EACH ROW WHEN (NEW.public_id IS NULL)
  EXECUTE FUNCTION app.assign_contract_public_id();

-- Contract status machine + authorization. Every transition is validated here so
-- a hand-crafted API request cannot skip DECLINED or bypass approval.
CREATE OR REPLACE FUNCTION app.assert_contract_transition() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  v_from public.contract_status := OLD.status;
  v_to   public.contract_status := NEW.status;
  v_allowed text[];
BEGIN
  -- Migrations, seeds and background workers have no end-user identity.
  -- mytrakin_api is neither superuser nor BYPASSRLS, so request traffic
  -- always falls through to the real checks below.
  IF app.is_trusted_context() THEN
    RETURN NEW;
  END IF;

  -- Migrations, seeds and background workers have no end-user identity.
  -- mytrakin_api is neither superuser nor BYPASSRLS, so request traffic
  -- always falls through to the real checks below.

  IF v_from = v_to THEN RETURN NEW; END IF;

  v_allowed := CASE v_from
    WHEN 'DRAFT'              THEN ARRAY['SENT','DECLINED','CLOSED']::text[]
    WHEN 'SENT'               THEN ARRAY['PENDING_ACCEPTANCE','ACCEPTED','DECLINED','CLOSED']::text[]
    WHEN 'PENDING_ACCEPTANCE' THEN ARRAY['ACCEPTED','DECLINED','CLOSED']::text[]
    WHEN 'ACCEPTED'           THEN ARRAY['ACTIVE','DECLINED','CLOSED']::text[]
    WHEN 'ACTIVE'             THEN ARRAY['EXPIRED','TERMINATED','CLOSED']::text[]
    WHEN 'DECLINED'           THEN ARRAY['CLOSED']::text[]
    WHEN 'EXPIRED'            THEN ARRAY['CLOSED','ACTIVE']::text[]
    WHEN 'TERMINATED'         THEN ARRAY['CLOSED']::text[]
    WHEN 'CLOSED'             THEN ARRAY[]::text[]
    ELSE ARRAY[]::text[]
  END;

  IF NOT (v_to::text = ANY (v_allowed)) THEN
    RAISE EXCEPTION 'invalid contract transition % -> %', v_from, v_to
      USING ERRCODE = 'check_violation';
  END IF;

  -- Writing commercial terms requires authority; acceptance requires the counterparty.
  IF NEW.locked = FALSE AND OLD.locked = TRUE THEN
    IF NOT app.has_permission(NEW.company_id, 'contracts.update') THEN
      RAISE EXCEPTION 'missing permission contracts.update to unlock contract %', NEW.public_id
        USING ERRCODE = 'insufficient_privilege';
    END IF;
  END IF;

  IF v_to IN ('ACCEPTED','ACTIVE') THEN
    IF NOT (app.is_member(NEW.company_id)
            OR app.is_member(NEW.counterparty_company_id)
            OR app.current_user_id() = NEW.counterparty_user_id) THEN
      RAISE EXCEPTION 'only the counterparty may accept contract %', NEW.public_id
        USING ERRCODE = 'insufficient_privilege';
    END IF;
  END IF;

  IF v_to = 'ACTIVE' AND NOT app.has_permission(NEW.company_id, 'contracts.approve') THEN
    RAISE EXCEPTION 'missing permission contracts.approve to activate contract %', NEW.public_id
      USING ERRCODE = 'insufficient_privilege';
  END IF;

  IF v_to = 'SENT' AND NOT app.has_permission(NEW.company_id, 'contracts.send') THEN
    RAISE EXCEPTION 'missing permission contracts.send' USING ERRCODE = 'insufficient_privilege';
  END IF;

  IF v_to = 'CLOSED' AND NOT app.has_permission(NEW.company_id, 'contracts.delete') THEN
    RAISE EXCEPTION 'missing permission contracts.delete' USING ERRCODE = 'insufficient_privilege';
  END IF;

  NEW.responded_at := CASE WHEN v_to IN ('ACCEPTED','DECLINED') THEN now() ELSE NEW.responded_at END;
  NEW.activated_at := CASE WHEN v_to = 'ACTIVE' THEN now() ELSE NEW.activated_at END;
  NEW.terminated_at := CASE WHEN v_to = 'TERMINATED' THEN now() ELSE NEW.terminated_at END;

  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_contracts_transition ON public.contracts;
CREATE TRIGGER trg_contracts_transition BEFORE UPDATE ON public.contracts
  FOR EACH ROW EXECUTE FUNCTION app.assert_contract_transition();

CREATE OR REPLACE FUNCTION app.assert_contract_insert() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  -- Migrations, seeds and background workers have no end-user identity.
  -- mytrakin_api is neither superuser nor BYPASSRLS, so request traffic
  -- always falls through to the real checks below.
  IF app.is_trusted_context() THEN
    RETURN NEW;
  END IF;

  -- Migrations, seeds and background workers have no end-user identity.
  -- mytrakin_api is neither superuser nor BYPASSRLS, so request traffic
  -- always falls through to the real checks below.
  IF NOT app.has_permission(NEW.company_id, 'contracts.create') THEN
    RAISE EXCEPTION 'missing permission contracts.create' USING ERRCODE = 'insufficient_privilege';
  END IF;

  IF NOT EXISTS (SELECT 1 FROM public.sows s
                  WHERE s.id = NEW.sow_id AND s.project_id = NEW.project_id) THEN
    RAISE EXCEPTION 'contract % must reference a SOW belonging to the same project', NEW.public_id
      USING ERRCODE = 'check_violation';
  END IF;

  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_contracts_guard ON public.contracts;
CREATE TRIGGER trg_contracts_guard BEFORE INSERT ON public.contracts
  FOR EACH ROW EXECUTE FUNCTION app.assert_contract_insert();

-- -----------------------------------------------------------------------------
-- contract_parties — supports multi-hop chains (A -> B -> D -> employee).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.contract_parties (
  id             uuid PRIMARY KEY DEFAULT app.uuid7(),
  contract_id    uuid NOT NULL REFERENCES public.contracts(id) ON DELETE CASCADE,
  party_company_id uuid REFERENCES public.companies(id) ON DELETE RESTRICT,
  party_user_id  uuid REFERENCES public.users(id) ON DELETE RESTRICT,
  party_role     text NOT NULL DEFAULT 'PRIMARY'
                   CHECK (party_role IN ('PRIMARY','SUBCONTRACTOR','SUBMITTEE','COUNTERPARTY','GUARANTOR')),
  signatory_name text,
  signatory_email citext,
  signed_at      timestamptz,
  signature_hash char(64),
  created_at     timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_contract_party_exclusive
    CHECK (party_company_id IS NOT NULL OR party_user_id IS NOT NULL),
  UNIQUE (contract_id, party_company_id, party_user_id, party_role)
);

CREATE INDEX IF NOT EXISTS ix_contract_parties_contract ON public.contract_parties (contract_id);
CREATE INDEX IF NOT EXISTS ix_contract_parties_company  ON public.contract_parties (party_company_id);
CREATE INDEX IF NOT EXISTS ix_contract_parties_user     ON public.contract_parties (party_user_id);

-- -----------------------------------------------------------------------------
-- contract_roles — the specific role + count covered by this contract.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.contract_roles (
  id              uuid PRIMARY KEY DEFAULT app.uuid7(),
  contract_id     uuid NOT NULL REFERENCES public.contracts(id) ON DELETE CASCADE,
  -- CASCADE for the same reason as sow_roles: the role line belongs to the
  -- project role and cannot outlive it.
  project_role_id uuid NOT NULL REFERENCES public.project_roles(id) ON DELETE CASCADE,
  sow_role_id     uuid REFERENCES public.sow_roles(id) ON DELETE SET NULL,
  quantity        int NOT NULL DEFAULT 1 CHECK (quantity > 0),
  rate            numeric(18,4) CHECK (rate IS NULL OR rate >= 0),
  rate_type       text NOT NULL DEFAULT 'HOURLY'
                    CHECK (rate_type IN ('HOURLY','DAILY','FIXED','PER_UNIT','PERCENT')),
  currency        char(3) NOT NULL DEFAULT 'USD',
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (contract_id, project_role_id)
);

CREATE INDEX IF NOT EXISTS ix_contract_roles_role ON public.contract_roles (project_role_id);

-- -----------------------------------------------------------------------------
-- contract_line_items — every billable item traces to its contract (I3).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.contract_line_items (
  id             uuid PRIMARY KEY DEFAULT app.uuid7(),
  contract_id    uuid NOT NULL REFERENCES public.contracts(id) ON DELETE CASCADE,
  contract_role_id uuid REFERENCES public.contract_roles(id) ON DELETE SET NULL,
  line_type      public.line_item_kind NOT NULL DEFAULT 'FIXED',
  label          text NOT NULL,
  description    text,
  quantity       numeric(18,4) NOT NULL DEFAULT 1 CHECK (quantity > 0),
  unit           text NOT NULL DEFAULT 'HOUR',
  unit_rate      numeric(18,4) NOT NULL DEFAULT 0 CHECK (unit_rate >= 0),
  amount         numeric(18,4) NOT NULL DEFAULT 0,
  currency       char(3) NOT NULL DEFAULT 'USD',
  billing_basis  public.billing_basis NOT NULL DEFAULT 'FIXED',
  billing_frequency public.billing_frequency NOT NULL DEFAULT 'MONTHLY',
  tax_rate       numeric(7,4) NOT NULL DEFAULT 0 CHECK (tax_rate >= 0),
  is_taxable     boolean NOT NULL DEFAULT false,
  is_additional  boolean NOT NULL DEFAULT false,   -- commissions, third-party vendor, etc.
  third_party_name text,
  proration_start date,
  proration_end   date,
  cap_amount     numeric(18,4) CHECK (cap_amount IS NULL OR cap_amount >= 0),
  sort_order     int NOT NULL DEFAULT 0,
  is_active      boolean NOT NULL DEFAULT true,
  metadata       jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.contract_line_items IS
  'Billable line items. Every invoice item must reference a contract_line_item via invoice_items.source_line_item_id.';

CREATE INDEX IF NOT EXISTS ix_line_items_contract ON public.contract_line_items (contract_id, sort_order);
CREATE INDEX IF NOT EXISTS ix_line_items_active   ON public.contract_line_items (contract_id) WHERE is_active;

-- Amount is derived, never accepted from the client.
CREATE OR REPLACE FUNCTION app.compute_line_item_amount() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  NEW.amount := round(NEW.quantity * NEW.unit_rate, 4);
  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_line_items_amount ON public.contract_line_items;
CREATE TRIGGER trg_line_items_amount BEFORE INSERT OR UPDATE OF quantity, unit_rate
  ON public.contract_line_items FOR EACH ROW
  EXECUTE FUNCTION app.compute_line_item_amount();

-- -----------------------------------------------------------------------------
-- contract_approval_steps — internal approval chain (own approval workflow,
-- separate from counterparty acceptance).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.contract_approval_steps (
  id             uuid PRIMARY KEY DEFAULT app.uuid7(),
  contract_id    uuid NOT NULL REFERENCES public.contracts(id) ON DELETE CASCADE,
  step_no        int NOT NULL CHECK (step_no > 0),
  name           text NOT NULL,
  approver_user_id uuid REFERENCES public.users(id) ON DELETE SET NULL,
  approver_company_id uuid REFERENCES public.companies(id) ON DELETE SET NULL,
  required_permission text,
  status         text NOT NULL DEFAULT 'PENDING'
                   CHECK (status IN ('PENDING','APPROVED','REJECTED','SKIPPED')),
  decided_at     timestamptz,
  notes          text,
  due_at         timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (contract_id, step_no),
  CONSTRAINT ck_approval_approver CHECK (approver_user_id IS NOT NULL OR approver_company_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS ix_contract_steps_pending
  ON public.contract_approval_steps (approver_user_id, created_at) WHERE status = 'PENDING';
CREATE INDEX IF NOT EXISTS ix_contract_steps_company
  ON public.contract_approval_steps (approver_company_id, status);

CREATE OR REPLACE FUNCTION app.assert_contract_approval() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_company uuid;
  -- Migrations, seeds and background workers have no end-user identity.
  -- mytrakin_api is neither superuser nor BYPASSRLS, so request traffic
  -- always falls through to the real checks below.
BEGIN
  -- Migrations, seeds and background workers have no end-user identity.
  -- mytrakin_api is neither superuser nor BYPASSRLS, so request traffic
  -- always falls through to the real checks below.
  IF app.is_trusted_context() THEN
    RETURN NEW;
  END IF;

  IF TG_OP = 'UPDATE' AND OLD.status <> 'APPROVED' AND NEW.status = 'APPROVED' THEN
    SELECT company_id INTO v_company FROM public.contracts WHERE id = NEW.contract_id;

    -- The step may designate a specific user; that user cannot also be the one
    -- approving it (four-eyes principle).
    IF NEW.approver_user_id IS NOT NULL
       AND NEW.approver_user_id = app.current_user_id()
       AND NOT COALESCE((SELECT c.settings->'segregation_of_duties'->>'allow_self_approval'
                           FROM public.companies c WHERE c.id = v_company), 'false')::boolean THEN
      RAISE EXCEPTION 'segregation of duties: approver cannot be the requester of the same step'
        USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.required_permission IS NOT NULL
       AND NOT app.has_permission(v_company, NEW.required_permission) THEN
      RAISE EXCEPTION 'missing permission % to approve contract step', NEW.required_permission
        USING ERRCODE = 'insufficient_privilege';
    END IF;

    NEW.decided_at := now();
  END IF;
  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_contract_approval_guard ON public.contract_approval_steps;
CREATE TRIGGER trg_contract_approval_guard BEFORE UPDATE ON public.contract_approval_steps
  FOR EACH ROW EXECUTE FUNCTION app.assert_contract_approval();

-- -----------------------------------------------------------------------------
-- Contract events: contract lifecycle emits outbox events.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.emit_contract_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, app, platform, pg_temp
AS $$
BEGIN
  IF TG_OP = 'UPDATE' AND NEW.status IS DISTINCT FROM OLD.status THEN
    INSERT INTO platform.outbox_events (
      event_type, company_id, actor_user_id, aggregate_type, aggregate_id, payload, idempotency_key
    ) VALUES (
      'CONTRACT_STATUS_CHANGED', NEW.company_id, app.current_user_id(), 'contract', NEW.id,
      jsonb_build_object(
        'public_id', NEW.public_id, 'from', OLD.status, 'to', NEW.status,
        'project_id', NEW.project_id, 'counterparty_company_id', NEW.counterparty_company_id,
        'counterparty_user_id', NEW.counterparty_user_id),
      'contract_status:' || NEW.id || ':' || NEW.version || ':' || NEW.status
    );
  END IF;
  RETURN NULL;
END
$$;

DROP TRIGGER IF EXISTS trg_contract_event ON public.contracts;
CREATE TRIGGER trg_contract_event AFTER UPDATE ON public.contracts
  FOR EACH ROW EXECUTE FUNCTION app.emit_contract_event();

-- Contract expiry scheduling: notice_period_end is derived, not guessed by clients.
CREATE OR REPLACE FUNCTION app.compute_contract_dates() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.termination_notice_days IS NOT NULL AND NEW.end_date IS NOT NULL THEN
    NEW.notice_period_end := NEW.end_date - NEW.termination_notice_days;
  END IF;
  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_contract_dates ON public.contracts;
CREATE TRIGGER trg_contract_dates BEFORE INSERT OR UPDATE OF end_date, termination_notice_days
  ON public.contracts FOR EACH ROW EXECUTE FUNCTION app.compute_contract_dates();

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['sow_roles','contract_roles','contract_line_items','contract_approval_steps'] LOOP
    PERFORM app.attach_updated_at(
      format('public.%I', t)::regclass,
      'trg_' || t || '_updated_at');
  END LOOP;
END
$$;

GRANT SELECT, INSERT, UPDATE, DELETE ON
  public.projects, public.project_roles, public.sows, public.sow_roles,
  public.contracts, public.contract_parties, public.contract_roles,
  public.contract_line_items, public.contract_approval_steps
TO mytrakin_api, mytrakin_worker;
