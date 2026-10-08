-- =============================================================================
-- MyTrakin :: 0015_commercial_terms_and_permissions.sql
--
-- Closes the gaps between the schema shipped in 0001-0014 and the product
-- contract the API and UI implement. Nothing here duplicates an existing table,
-- column or function; each addition is required by an invariant the earlier
-- migrations could not express.
--
-- Added
--   1. Permission keys the catalogue is missing (contracts.terminate,
--      timesheets.reject, assignments.*, sows.submit, leave.create, invoices.send,
--      ai.read, ai.actions, ai.automations) plus their grants on role templates.
--   2. app.assign_leave_policy_public_id() -- leave_policies.public_id was NOT NULL
--      UNIQUE with no generator, so the table was uninsertable.
--   3. app.next_invoice_number() + trigger -- invoice_number was nullable,
--      unconstrained and never assigned anywhere.
--   4. contracts.contract_value -- the SOW ceiling had no contract-side mirror.
--   5. contract_roles commercial terms (billing model, payment terms, billing
--      frequency, maximum units, window dates, overtime/tax rules). The billing
--      engine needs these to price approved hours authoritatively.
--   6. project_roles commercial columns (cost rate, currency, billing basis,
--      allocation default, window dates) so a role can be budgeted.
--   7. app.assert_timesheet_locked() -- rule 6/rule 3 ("approved timesheets
--      cannot be modified without a controlled revision process") was only
--      enforced in application code, which the database cannot verify.
--   8. app.next_revision_no() is now driven by a trigger so every entry mutation
--      on a non-draft timesheet is captured in timesheet_revisions.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. Permission catalogue completion
-- -----------------------------------------------------------------------------
INSERT INTO public.permissions (key, module, action, description, sensitivity, requires_approval) VALUES
('sows.submit',            'code',     'create', 'Submit a SOW into the approval workflow',           'STANDARD',   false),
('contracts.terminate',    'code',     'delete', 'Terminate an active contract',                       'DESTRUCTIVE',true),
('assignments.read',       'work',     'read',   'View project and contract assignments',              'STANDARD',   false),
('assignments.create',     'work',     'create', 'Assign a person to a project role',                  'STANDARD',   false),
('assignments.update',     'work',     'update', 'Change or end an assignment',                        'STANDARD',   false),
('timesheets.update',      'work',     'update', 'Edit a draft or rejected timesheet',                  'STANDARD',   false),
('timesheets.reject',      'work',     'update', 'Reject a submitted timesheet',                        'SENSITIVE',  true),
('leave.create',           'work',     'create', 'Request leave',                                       'STANDARD',   false),
('invoices.send',          'billing',  'update', 'Send an approved invoice to the customer',           'SENSITIVE',  true),
('ai.read',                'ai',       'read',   'Read AI insights, extractions and knowledge',        'STANDARD',   false),
('ai.actions',             'ai',       'create', 'Propose AI actions for human approval',              'SENSITIVE',  true),
('ai.automations',         'ai',       'create', 'Create and run AI automations',                      'SENSITIVE',  true)
ON CONFLICT (key) DO NOTHING;

-- Grants. Each new key rides alongside the sibling key that already expressed the
-- same authority, so no existing member loses access and segregation of duties
-- is preserved (a template that never had approve/read never gains it here).
UPDATE public.role_templates SET permission_keys = permission_keys || ARRAY[
  'sows.submit', 'contracts.terminate', 'assignments.read', 'assignments.create',
  'assignments.update', 'timesheets.update', 'timesheets.reject', 'leave.create',
  'invoices.send', 'ai.read', 'ai.actions', 'ai.automations'
]
WHERE key = 'SUPER_ADMIN';

UPDATE public.role_templates SET permission_keys = permission_keys || ARRAY[
  'sows.submit', 'contracts.terminate', 'assignments.read', 'assignments.create',
  'assignments.update', 'timesheets.update', 'ai.read', 'ai.actions', 'ai.automations'
]
WHERE key IN ('CONTRACT_MANAGER', 'PROJECT_MANAGER');

UPDATE public.role_templates SET permission_keys = permission_keys || ARRAY[
  'assignments.read', 'assignments.create', 'assignments.update', 'timesheets.update',
  'timesheets.reject', 'leave.create', 'ai.read'
]
WHERE key IN ('HR_MANAGER', 'TIMESHEET_MANAGER');

UPDATE public.role_templates SET permission_keys = permission_keys || ARRAY[
  'assignments.read', 'timesheets.update', 'leave.create', 'invoices.send', 'ai.read'
]
WHERE key = 'EMPLOYEE';

UPDATE public.role_templates SET permission_keys = permission_keys || ARRAY[
  'assignments.read', 'invoices.send', 'ai.read'
]
WHERE key = 'FINANCE_MANAGER';

-- VIEWER and COMPANY_ADMIN deliberately stay read-only: nothing is added.

-- -----------------------------------------------------------------------------
-- 2. leave_policies.public_id was NOT NULL UNIQUE with no generator
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.assign_leave_policy_public_id() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_try int := 0; v_id text;
BEGIN
  LOOP
    v_id := app.gen_public_id('LP', 8);
    EXIT WHEN NOT EXISTS (SELECT 1 FROM public.leave_policies WHERE public_id = v_id);
    v_try := v_try + 1;
    IF v_try > 5 THEN RAISE EXCEPTION 'could not allocate leave_policy public_id'; END IF;
  END LOOP;
  NEW.public_id := v_id;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_leave_policies_public_id ON public.leave_policies;
CREATE TRIGGER trg_leave_policies_public_id BEFORE INSERT ON public.leave_policies
  FOR EACH ROW WHEN (NEW.public_id IS NULL)
  EXECUTE FUNCTION app.assign_leave_policy_public_id();

SELECT app.attach_triggers('public.leave_policies'::regclass);

-- -----------------------------------------------------------------------------
-- 3. invoice_number was never assigned
-- -----------------------------------------------------------------------------
-- Gap-free per company and year. The advisory lock serialises concurrent
-- generators so two requests cannot read the same counter value.
CREATE OR REPLACE FUNCTION app.next_invoice_number(p_company_id uuid) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE v_year int := EXTRACT(YEAR FROM CURRENT_DATE)::int; v_next int;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended(p_company_id::text || ':' || v_year::text, 0));
  SELECT COALESCE(MAX(SUBSTRING(i.invoice_number FROM '([0-9]+)$')::int), 0) + 1
    INTO v_next
    FROM public.invoices i
   WHERE i.company_id = p_company_id
     AND i.deleted_at IS NULL
     AND i.invoice_number LIKE 'INV-' || v_year::text || '-%';
  RETURN 'INV-' || v_year::text || '-' || LPAD(v_next::text, 5, '0');
END $$;

CREATE OR REPLACE FUNCTION app.assign_invoice_number() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.invoice_number IS NULL OR NEW.invoice_number = '' THEN
    NEW.invoice_number := app.next_invoice_number(NEW.company_id);
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_invoices_number ON public.invoices;
CREATE TRIGGER trg_invoices_number BEFORE INSERT ON public.invoices
  FOR EACH ROW EXECUTE FUNCTION app.assign_invoice_number();

-- -----------------------------------------------------------------------------
-- 4. contracts.contract_value
-- -----------------------------------------------------------------------------
ALTER TABLE public.contracts
  ADD COLUMN IF NOT EXISTS contract_value numeric(18,4);

ALTER TABLE public.contracts
  DROP CONSTRAINT IF EXISTS ck_contract_value;
ALTER TABLE public.contracts
  ADD CONSTRAINT ck_contract_value CHECK (contract_value IS NULL OR contract_value >= 0);

-- -----------------------------------------------------------------------------
-- 5. contract_roles commercial terms (authoritative for billing)
-- -----------------------------------------------------------------------------
ALTER TABLE public.contract_roles
  ADD COLUMN IF NOT EXISTS billing_basis          public.billing_basis   NOT NULL DEFAULT 'TIMESHEET';
ALTER TABLE public.contract_roles
  ADD COLUMN IF NOT EXISTS billing_frequency     public.billing_frequency NOT NULL DEFAULT 'MONTHLY';
ALTER TABLE public.contract_roles
  ADD COLUMN IF NOT EXISTS payment_terms_days     int                    NOT NULL DEFAULT 30;
ALTER TABLE public.contract_roles
  ADD COLUMN IF NOT EXISTS max_units              numeric(18,4);
ALTER TABLE public.contract_roles
  ADD COLUMN IF NOT EXISTS start_date             date;
ALTER TABLE public.contract_roles
  ADD COLUMN IF NOT EXISTS end_date               date;
ALTER TABLE public.contract_roles
  ADD COLUMN IF NOT EXISTS overtime_rule          text                   NOT NULL DEFAULT 'NONE';
ALTER TABLE public.contract_roles
  ADD COLUMN IF NOT EXISTS overtime_rate_multiplier numeric(6,3)         NOT NULL DEFAULT 1.000;
ALTER TABLE public.contract_roles
  ADD COLUMN IF NOT EXISTS tax_rule               text                   NOT NULL DEFAULT 'STANDARD';
ALTER TABLE public.contract_roles
  ADD COLUMN IF NOT EXISTS tax_rate               numeric(7,4)           NOT NULL DEFAULT 0;
ALTER TABLE public.contract_roles
  ADD COLUMN IF NOT EXISTS notes                  text;

ALTER TABLE public.contract_roles
  DROP CONSTRAINT IF EXISTS ck_contract_role_terms;
ALTER TABLE public.contract_roles
  ADD CONSTRAINT ck_contract_role_terms CHECK (
    (max_units IS NULL OR max_units >= 0)
    AND (overtime_rate_multiplier >= 1)
    AND (tax_rate >= 0)
    AND (payment_terms_days >= 0)
    AND (end_date IS NULL OR start_date IS NULL OR end_date >= start_date)
    AND overtime_rule  IN ('NONE','MULTIPLIER','FLAT')
    AND tax_rule      IN ('NONE','STANDARD','REDUCED','ZERO','EXEMPT','REVERSE_CHARGE')
  );

-- The role must belong to the same project as the contract. A cross-project
-- contract_role would let a timesheet be billed against an unrelated rate.
CREATE OR REPLACE FUNCTION app.assert_contract_role_project() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_ok boolean;
BEGIN
  SELECT EXISTS (
    SELECT 1
      FROM public.contract_roles cr
      JOIN public.contracts     c  ON c.id  = cr.contract_id
      JOIN public.project_roles pr ON pr.id = cr.project_role_id
     WHERE cr.id = NEW.id
       AND pr.project_id = c.project_id
       AND pr.company_id = c.company_id
  ) INTO v_ok;
  IF NOT v_ok THEN
    RAISE EXCEPTION 'contract_role_project_mismatch'
      USING ERRCODE = 'restrict_violation',
            HINT   = 'The project role must belong to the same project and company as the contract.';
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_contract_roles_project ON public.contract_roles;
CREATE CONSTRAINT TRIGGER trg_contract_roles_project
  AFTER INSERT OR UPDATE OF contract_id, project_role_id ON public.contract_roles
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION app.assert_contract_role_project();

-- -----------------------------------------------------------------------------
-- 6. project_roles commercial columns
-- -----------------------------------------------------------------------------
ALTER TABLE public.project_roles
  ADD COLUMN IF NOT EXISTS cost_rate      numeric(18,4);
ALTER TABLE public.project_roles
  ADD COLUMN IF NOT EXISTS currency       char(3) NOT NULL DEFAULT 'USD';
ALTER TABLE public.project_roles
  ADD COLUMN IF NOT EXISTS billing_basis  public.billing_basis NOT NULL DEFAULT 'TIMESHEET';
ALTER TABLE public.project_roles
  ADD COLUMN IF NOT EXISTS allocation_pct numeric(5,2) NOT NULL DEFAULT 100;
ALTER TABLE public.project_roles
  ADD COLUMN IF NOT EXISTS start_date     date;
ALTER TABLE public.project_roles
  ADD COLUMN IF NOT EXISTS end_date       date;

ALTER TABLE public.project_roles
  DROP CONSTRAINT IF EXISTS ck_project_role_currency;
ALTER TABLE public.project_roles
  ADD CONSTRAINT ck_project_role_currency CHECK (currency ~ '^[A-Z]{3}$');

ALTER TABLE public.project_roles
  DROP CONSTRAINT IF EXISTS ck_project_role_allocation_pct;
ALTER TABLE public.project_roles
  ADD CONSTRAINT ck_project_role_allocation_pct CHECK (allocation_pct > 0 AND allocation_pct <= 100);

ALTER TABLE public.project_roles
  DROP CONSTRAINT IF EXISTS ck_project_role_dates;
ALTER TABLE public.project_roles
  ADD CONSTRAINT ck_project_role_dates CHECK (end_date IS NULL OR start_date IS NULL OR end_date >= start_date);

-- The role must belong to the project it is filed under (same tenant).
CREATE OR REPLACE FUNCTION app.assert_project_role_scope() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_ok boolean;
BEGIN
  SELECT EXISTS (
    SELECT 1 FROM public.projects p
     WHERE p.id = NEW.project_id AND p.company_id = NEW.company_id
  ) INTO v_ok;
  IF NOT v_ok THEN
    RAISE EXCEPTION 'project_role_scope_mismatch'
      USING ERRCODE = 'restrict_violation',
            HINT   = 'A project role must belong to the project it is created under.';
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_project_roles_scope ON public.project_roles;
CREATE CONSTRAINT TRIGGER trg_project_roles_scope
  AFTER INSERT OR UPDATE OF project_id, company_id ON public.project_roles
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION app.assert_project_role_scope();

-- -----------------------------------------------------------------------------
-- 7 + 8. Timesheet lock + revision capture
-- -----------------------------------------------------------------------------
-- Rule 3 and rule 6. Application code could forget this; the database cannot.
CREATE OR REPLACE FUNCTION app.assert_timesheet_editable() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_status public.timesheet_status; v_locked boolean; v_context text;
BEGIN
  SELECT ts.status, ts.locked INTO v_status, v_locked
    FROM public.timesheets ts WHERE ts.id = COALESCE(NEW.timesheet_id, OLD.timesheet_id);

  IF v_status IS NULL THEN RETURN NULL; END IF;   -- parent gone; CASCADE handles it

  IF TG_OP = 'DELETE' THEN
    IF v_status IN ('APPROVED', 'LOCKED') OR v_locked THEN
      RAISE EXCEPTION 'timesheet_locked'
        USING ERRCODE = 'restrict_violation',
              HINT   = 'An approved or locked timesheet cannot have entries removed.';
    END IF;
    RETURN OLD;
  END IF;

  -- A company policy may require the trusted (migration / worker) context to
  -- amend a locked sheet; ordinary requests never get that path.
  v_context := current_setting('app.actor_type', true);
  IF (v_status IN ('APPROVED', 'LOCKED') OR v_locked) AND v_context IS DISTINCT FROM 'SYSTEM' THEN
    RAISE EXCEPTION 'timesheet_locked'
      USING ERRCODE = 'restrict_violation',
              HINT   = 'An approved or locked timesheet cannot be edited. Raise a revision instead.';
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_entries_timesheet_editable ON public.timesheet_entries;
CREATE TRIGGER trg_entries_timesheet_editable
  BEFORE INSERT OR UPDATE OR DELETE ON public.timesheet_entries
  FOR EACH ROW EXECUTE FUNCTION app.assert_timesheet_editable();

-- Rule 6, revision half. Every entry mutation on a non-draft sheet leaves a
-- row in timesheet_revisions, so an approved timesheet is always amendable
-- through a reviewable trail rather than a silent UPDATE.
CREATE OR REPLACE FUNCTION app.capture_timesheet_revision() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  v_timesheet_id uuid := COALESCE(NEW.timesheet_id, OLD.timesheet_id);
  v_status       public.timesheet_status;
  v_change       text;
  v_summary      text;
BEGIN
  SELECT ts.status INTO v_status FROM public.timesheets ts WHERE ts.id = v_timesheet_id;
  -- Draft edits are the author's own working copy; only reviewed sheets need a trail.
  IF v_status IS NULL OR v_status = 'DRAFT' THEN RETURN NULL; END IF;

  v_change := CASE TG_OP
    WHEN 'INSERT' THEN 'ENTRY_ADDED'
    WHEN 'DELETE' THEN 'ENTRY_REMOVED'
    ELSE 'ENTRY_UPDATED'
  END;

  v_summary := CASE TG_OP
    WHEN 'INSERT' THEN 'Entry ' || NEW.entry_date || ' (' || NEW.hours || 'h) added'
    WHEN 'DELETE' THEN 'Entry ' || OLD.entry_date || ' (' || OLD.hours || 'h) removed'
    ELSE 'Entry ' || COALESCE(NEW.entry_date, OLD.entry_date) || ' updated'
  END;

  INSERT INTO public.timesheet_revisions
    (timesheet_id, revision_no, changed_by, change_type, summary, snapshot, reason)
  VALUES
    (v_timesheet_id,
     app.next_revision_no(v_timesheet_id),
     app.current_user_id(),
     v_change,
     v_summary,
     jsonb_build_object(
       'entry_date',   COALESCE(NEW.entry_date, OLD.entry_date),
       'hours',        COALESCE(NEW.hours, OLD.hours),
       'is_billable',  COALESCE(NEW.is_billable, OLD.is_billable),
       'description',  COALESCE(NEW.work_description, OLD.work_description),
       'timesheet_status', v_status
     ),
     NULL);

  RETURN NULL;
END $$;

DROP TRIGGER IF EXISTS trg_entries_capture_revision ON public.timesheet_entries;
CREATE TRIGGER trg_entries_capture_revision
  AFTER INSERT OR UPDATE OR DELETE ON public.timesheet_entries
  FOR EACH ROW EXECUTE FUNCTION app.capture_timesheet_revision();

-- Grants for the new columns: mytrakin_api holds table-level grants on these
-- relations, so a new column is readable automatically. Nothing to re-grant.