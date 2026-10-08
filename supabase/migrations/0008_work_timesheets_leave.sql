-- =============================================================================
-- MyTrakin :: 0008_work_timesheets_leave.sql
-- WORK module: assignments, timesheets, entries, revisions, approval chains, leave.
--
-- Invariants enforced in the database:
--   W1  a timesheet must belong to an ACCEPTED/ACTIVE contract (no orphan work)
--   W2  entries may not be written into a LOCKED timesheet
--   W3  approved/locked timesheets cannot be silently mutated; corrections create
--       a revision with full prior-state capture
--   W4  segregation of duties: an approver cannot approve their own submission
--   W5  hours are derived from times, never accepted from the client
-- =============================================================================

-- -----------------------------------------------------------------------------
-- assignments — a person attached to a contract+role. This is the join that makes
-- "no orphan work records" structural: timesheets require an assignment.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.assignments (
  id              uuid PRIMARY KEY DEFAULT app.uuid7(),
  contract_id     uuid NOT NULL REFERENCES public.contracts(id) ON DELETE CASCADE,
  contract_role_id uuid REFERENCES public.contract_roles(id) ON DELETE SET NULL,
  project_id      uuid NOT NULL REFERENCES public.projects(id) ON DELETE RESTRICT,
  company_id      uuid NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
  user_id         uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  role_title      text NOT NULL,
  hourly_rate     numeric(18,4) CHECK (hourly_rate IS NULL OR hourly_rate >= 0),
  currency        char(3) NOT NULL DEFAULT 'USD',
  start_date      date NOT NULL,
  end_date        date,
  status          text NOT NULL DEFAULT 'PENDING'
                    CHECK (status IN ('PENDING','ACTIVE','ON_LEAVE','COMPLETED','TERMINATED')),
  allocation_pct  numeric(5,2) NOT NULL DEFAULT 100
                    CHECK (allocation_pct > 0 AND allocation_pct <= 100),
  source          text NOT NULL DEFAULT 'CONTRACT'
                    CHECK (source IN ('CONTRACT','SOW','MIGRATION','MANUAL')),
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_assignment_dates CHECK (end_date IS NULL OR end_date >= start_date)
);

CREATE INDEX IF NOT EXISTS ix_assignments_user
  ON public.assignments (user_id, status, end_date);
CREATE INDEX IF NOT EXISTS ix_assignments_contract ON public.assignments (contract_id, status);
CREATE INDEX IF NOT EXISTS ix_assignments_company  ON public.assignments (company_id, status);
CREATE INDEX IF NOT EXISTS ix_assignments_project  ON public.assignments (project_id, status);

-- An assignment may only be created against a live contract (W1).
CREATE OR REPLACE FUNCTION app.assert_assignment_contract() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_status public.contract_status; v_company uuid;
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

  SELECT c.status, c.company_id INTO v_status, v_company
    FROM public.contracts c WHERE c.id = NEW.contract_id;

  IF v_status IS NULL THEN
    RAISE EXCEPTION 'assignment references a non-existent contract' USING ERRCODE = 'foreign_key_violation';
  END IF;

  IF v_status NOT IN ('ACCEPTED','ACTIVE') THEN
    RAISE EXCEPTION 'contract % is %; work requires an ACCEPTED or ACTIVE contract',
      NEW.contract_id, v_status USING ERRCODE = 'check_violation';
  END IF;

  IF NOT (app.is_member(v_company) OR app.current_user_id() = NEW.user_id) THEN
    RAISE EXCEPTION 'cannot assign a user to a contract you are not party to'
      USING ERRCODE = 'insufficient_privilege';
  END IF;

  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_assignment_contract ON public.assignments;
CREATE TRIGGER trg_assignment_contract BEFORE INSERT ON public.assignments
  FOR EACH ROW EXECUTE FUNCTION app.assert_assignment_contract();

-- Acceptance activates pending assignments automatically.
CREATE OR REPLACE FUNCTION app.activate_assignments_on_contract() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, app, pg_temp
AS $$
BEGIN
  -- Migrations, seeds and background workers have no end-user identity.
  -- mytrakin_api is neither superuser nor BYPASSRLS, so request traffic
  -- always falls through to the real checks below.
  IF app.is_trusted_context() THEN
    RETURN NEW;
  END IF;

  IF NEW.status IN ('ACCEPTED','ACTIVE') AND OLD.status NOT IN ('ACCEPTED','ACTIVE') THEN
    UPDATE public.assignments
       SET status = CASE WHEN status = 'PENDING' THEN 'ACTIVE' ELSE status END
     WHERE contract_id = NEW.id;
  END IF;
  IF NEW.status IN ('TERMINATED','CLOSED','DECLINED') THEN
    UPDATE public.assignments SET status = 'TERMINATED' WHERE contract_id = NEW.id;
  END IF;
  RETURN NULL;
END
$$;

DROP TRIGGER IF EXISTS trg_assignments_activate ON public.contracts;
CREATE TRIGGER trg_assignments_activate AFTER UPDATE OF status ON public.contracts
  FOR EACH ROW WHEN (NEW.status IS DISTINCT FROM OLD.status)
  EXECUTE FUNCTION app.activate_assignments_on_contract();

-- Can this user perform timesheet work under this contract right now?
CREATE OR REPLACE FUNCTION app.can_record_time(
  p_user_id uuid,
  p_contract_id uuid,
  p_work_date date DEFAULT current_date
) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, app, pg_temp
AS $$
  SELECT EXISTS (
    SELECT 1
      FROM public.assignments a
      JOIN public.contracts c ON c.id = a.contract_id
     WHERE a.user_id = p_user_id
       AND a.contract_id = p_contract_id
       AND a.status IN ('ACTIVE','ON_LEAVE')
       AND c.status IN ('ACCEPTED','ACTIVE')
       AND c.deleted_at IS NULL
       AND a.start_date <= p_work_date
       AND (a.end_date IS NULL OR a.end_date >= p_work_date)
  );
$$;

COMMENT ON FUNCTION app.can_record_time(uuid, uuid, date) IS
  'Work-eligibility predicate. Timesheet writes are rejected unless this returns true (W1).';

-- -----------------------------------------------------------------------------
-- timesheets — one per user per period. Period boundaries drive billing.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.timesheets (
  id               uuid PRIMARY KEY DEFAULT app.uuid7(),
  public_id        text NOT NULL UNIQUE,
  user_id          uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  company_id       uuid NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
  assignment_id    uuid NOT NULL REFERENCES public.assignments(id) ON DELETE RESTRICT,
  contract_id      uuid NOT NULL REFERENCES public.contracts(id) ON DELETE RESTRICT,
  contract_role_id uuid REFERENCES public.contract_roles(id) ON DELETE SET NULL,
  project_id       uuid NOT NULL REFERENCES public.projects(id) ON DELETE RESTRICT,
  period_start     date NOT NULL,
  period_end       date NOT NULL,
  billing_frequency public.billing_frequency NOT NULL DEFAULT 'WEEKLY',
  status           public.timesheet_status NOT NULL DEFAULT 'DRAFT',
  total_hours      numeric(10,4) NOT NULL DEFAULT 0,
  billable_hours   numeric(10,4) NOT NULL DEFAULT 0,
  total_amount     numeric(18,4) NOT NULL DEFAULT 0,
  currency         char(3) NOT NULL DEFAULT 'USD',
  entry_count      int NOT NULL DEFAULT 0,
  current_step     int NOT NULL DEFAULT 0,
  locked_at        timestamptz,
  locked_by        uuid REFERENCES public.users(id) ON DELETE SET NULL,
  submitted_at     timestamptz,
  approved_at      timestamptz,
  rejection_reason text,
  adjustment_of    uuid REFERENCES public.timesheets(id) ON DELETE SET NULL,
  is_adjustment    boolean NOT NULL DEFAULT false,
  ai_imported      boolean NOT NULL DEFAULT false,
  ai_import_batch  text,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_timesheet_period CHECK (period_end >= period_start),
  CONSTRAINT ck_timesheet_hours   CHECK (total_hours >= 0 AND billable_hours >= 0 AND billable_hours <= total_hours),
  UNIQUE (user_id, assignment_id, period_start, period_end)
);

COMMENT ON TABLE public.timesheets IS
  'Timesheet period for one assignment. UNIQUE(user, assignment, period) makes duplicate periods impossible.';
COMMENT ON COLUMN public.timesheets.is_adjustment IS
  'A correction sheet created instead of mutating a locked sheet, preserving the audit trail (W3).';

CREATE INDEX IF NOT EXISTS ix_timesheets_user     ON public.timesheets (user_id, period_start DESC);
CREATE INDEX IF NOT EXISTS ix_timesheets_company  ON public.timesheets (company_id, status, period_start DESC);
CREATE INDEX IF NOT EXISTS ix_timesheets_contract ON public.timesheets (contract_id, status);
CREATE INDEX IF NOT EXISTS ix_timesheets_project  ON public.timesheets (project_id, status);
CREATE INDEX IF NOT EXISTS ix_timesheets_billing
  ON public.timesheets (contract_id, status, period_end)
  WHERE status = 'APPROVED';
-- pending-approval work queues
CREATE INDEX IF NOT EXISTS ix_timesheets_pending_company
  ON public.timesheets (company_id, submitted_at) WHERE status IN ('SUBMITTED','UNDER_REVIEW');
CREATE INDEX IF NOT EXISTS ix_timesheets_pending_user
  ON public.timesheets (user_id, status) WHERE status = 'REJECTED';

CREATE OR REPLACE FUNCTION app.assign_timesheet_public_id() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_try int := 0; v_id text;
BEGIN

  LOOP
    v_id := app.gen_public_id('TS', 8);
    EXIT WHEN NOT EXISTS (SELECT 1 FROM public.timesheets WHERE public_id = v_id);
    v_try := v_try + 1;
    IF v_try > 5 THEN RAISE EXCEPTION 'could not allocate timesheet public_id'; END IF;
  END LOOP;
  NEW.public_id := v_id;
  RETURN NEW;
END
$$;

SELECT app.attach_triggers('public.timesheets'::regclass);
DROP TRIGGER IF EXISTS trg_timesheets_public_id ON public.timesheets;
CREATE TRIGGER trg_timesheets_public_id BEFORE INSERT ON public.timesheets
  FOR EACH ROW WHEN (NEW.public_id IS NULL)
  EXECUTE FUNCTION app.assign_timesheet_public_id();

-- W1 at the timesheet level + author guard.
CREATE OR REPLACE FUNCTION app.assert_timesheet_insert() RETURNS trigger
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
  IF app.current_user_id() IS NOT NULL AND NEW.user_id <> app.current_user_id()
     AND NOT app.has_permission(NEW.company_id, 'timesheets.create_any') THEN
    RAISE EXCEPTION 'may only create your own timesheets'
      USING ERRCODE = 'insufficient_privilege';
  END IF;

  IF NOT app.can_record_time(NEW.user_id, NEW.contract_id, NEW.period_start) THEN
    RAISE EXCEPTION 'no active assignment links user % to contract % for period starting %',
      NEW.user_id, NEW.contract_id, NEW.period_start
      USING ERRCODE = 'check_violation',
            HINT = 'Work records require an accepted/active contract and a live assignment.';
  END IF;

  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_timesheets_guard ON public.timesheets;
CREATE TRIGGER trg_timesheets_guard BEFORE INSERT ON public.timesheets
  FOR EACH ROW EXECUTE FUNCTION app.assert_timesheet_insert();

-- W2 + W3 + W4: the timesheet state machine.
CREATE OR REPLACE FUNCTION app.assert_timesheet_transition() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  v_from public.timesheet_status := OLD.status;
  v_to   public.timesheet_status := NEW.status;
  v_allowed text[];
  v_sod   boolean;
  v_requested_by uuid;
  v_active_approver uuid;
BEGIN
  -- Migrations, seeds and background workers have no end-user identity.
  -- mytrakin_api is neither superuser nor BYPASSRLS, so request traffic
  -- always falls through to the real checks below.
  IF app.is_trusted_context() THEN
    RETURN NEW;
  END IF;

  IF v_from = v_to THEN
    -- Totals may only change while the sheet is editable.
    IF NOT (OLD.total_hours IS NOT DISTINCT FROM NEW.total_hours
            AND OLD.billable_hours IS NOT DISTINCT FROM NEW.billable_hours
            AND OLD.total_amount IS NOT DISTINCT FROM NEW.total_amount) THEN
      IF OLD.status IN ('APPROVED','LOCKED','UNDER_REVIEW') THEN
        RAISE EXCEPTION 'timesheet % is %; totals cannot be modified. Create an adjustment instead.',
          OLD.public_id, OLD.status USING ERRCODE = 'check_violation';
      END IF;
    END IF;
    RETURN NEW;
  END IF;
  -- Migrations, seeds and background workers have no end-user identity.
  -- mytrakin_api is neither superuser nor BYPASSRLS, so request traffic
  -- always falls through to the real checks below.
  v_allowed := CASE v_from
    WHEN 'DRAFT'       THEN ARRAY['SUBMITTED']::text[]
    WHEN 'SUBMITTED'   THEN ARRAY['UNDER_REVIEW','APPROVED','REJECTED','DRAFT']::text[]
    WHEN 'UNDER_REVIEW'THEN ARRAY['APPROVED','REJECTED','SUBMITTED']::text[]
    WHEN 'REJECTED'    THEN ARRAY['DRAFT','SUBMITTED']::text[]
    WHEN 'APPROVED'    THEN ARRAY['LOCKED']::text[]
    WHEN 'LOCKED'      THEN ARRAY[]::text[]
    ELSE ARRAY[]::text[]
  END;

  IF NOT (v_to::text = ANY (v_allowed)) THEN
    RAISE EXCEPTION 'invalid timesheet transition % -> %', v_from, v_to
      USING ERRCODE = 'check_violation';
  END IF;

  SELECT COALESCE((c.settings->'segregation_of_duties'->>'allow_self_approval'), 'false')::boolean,
         a.user_id
    INTO v_sod, v_requested_by
    FROM public.companies c
    LEFT JOIN public.assignments a ON a.id = NEW.assignment_id
   WHERE c.id = NEW.company_id;

  -- W4: self-approval defence.
  IF v_to IN ('APPROVED','LOCKED') AND NEW.user_id = app.current_user_id() AND NOT v_sod THEN
    RAISE EXCEPTION 'segregation of duties: you cannot approve your own timesheet %', NEW.public_id
      USING ERRCODE = 'insufficient_privilege';
  END IF;

  IF v_to IN ('APPROVED','LOCKED') THEN
    IF NOT app.has_permission(NEW.company_id, 'timesheets.approve') THEN
      RAISE EXCEPTION 'missing permission timesheets.approve' USING ERRCODE = 'insufficient_privilege';
    END IF;

    -- Must have traversed the configured approval chain. jsonb_array_length is
    -- applied to the array itself, not to the surrounding subquery.
    IF v_to = 'LOCKED' AND COALESCE(
         (SELECT jsonb_array_length(c.timesheet_approval_chain -> 'steps')
            FROM public.contracts c WHERE c.id = NEW.contract_id), 0) > 0
       THEN
      SELECT count(*) INTO v_active_approver
        FROM public.timesheet_approvals ta
       WHERE ta.timesheet_id = NEW.id AND ta.status = 'PENDING';
      IF v_active_approver > 0 THEN
        RAISE EXCEPTION 'timesheet % still has % pending approval step(s)', NEW.public_id, v_active_approver
          USING ERRCODE = 'check_violation';
      END IF;
    END IF;
  END IF;

  IF v_to = 'SUBMITTED' THEN
    IF NEW.user_id <> app.current_user_id() AND NOT app.has_permission(NEW.company_id, 'timesheets.submit') THEN
      RAISE EXCEPTION 'missing permission timesheets.submit' USING ERRCODE = 'insufficient_privilege';
    END IF;
    NEW.submitted_at := now();
  END IF;

  IF v_to = 'APPROVED' THEN NEW.approved_at := now(); END IF;
  IF v_to = 'LOCKED'   THEN NEW.locked_at := now(); NEW.locked_by := app.current_user_id(); END IF;

  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_timesheets_transition ON public.timesheets;
CREATE TRIGGER trg_timesheets_transition BEFORE UPDATE ON public.timesheets
  FOR EACH ROW EXECUTE FUNCTION app.assert_timesheet_transition();

-- -----------------------------------------------------------------------------
-- timesheet_entries — daily rows. Hours are COMPUTED (W5).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.timesheet_entries (
  id              uuid PRIMARY KEY DEFAULT app.uuid7(),
  timesheet_id    uuid NOT NULL REFERENCES public.timesheets(id) ON DELETE CASCADE,
  entry_date      date NOT NULL,
  start_time      time,
  end_time        time,
  break_minutes   int NOT NULL DEFAULT 0 CHECK (break_minutes >= 0 AND break_minutes <= 720),
  hours           numeric(10,4) NOT NULL DEFAULT 0 CHECK (hours >= 0 AND hours <= 24),
  is_billable     boolean NOT NULL DEFAULT true,
  work_description text NOT NULL DEFAULT '' CHECK (length(work_description) <= 2000),
  project_task    text,
  rate_applied    numeric(18,4) CHECK (rate_applied IS NULL OR rate_applied >= 0),
  amount          numeric(18,4) NOT NULL DEFAULT 0,
  currency        char(3) NOT NULL DEFAULT 'USD',
  -- provenance for AI imports: never silently trusted, always user-confirmed
  source          text NOT NULL DEFAULT 'MANUAL'
                    CHECK (source IN ('MANUAL','AI_IMPORT','BULK_EDIT','API')),
  ai_confidence   numeric(5,4) CHECK (ai_confidence IS NULL
                                     OR (ai_confidence >= 0 AND ai_confidence <= 1)),
  ai_source_ref   text,
  confirmed_by    uuid REFERENCES public.users(id) ON DELETE SET NULL,
  confirmed_at    timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (timesheet_id, entry_date, start_time),
  CONSTRAINT ck_entry_time_order CHECK (start_time IS NULL OR end_time IS NULL OR end_time > start_time)
);

COMMENT ON COLUMN public.timesheet_entries.source IS
  'MANUAL | AI_IMPORT | BULK_EDIT | API. AI_IMPORT rows require user confirmation before submission.';
COMMENT ON COLUMN public.timesheet_entries.rate_applied IS
  'Rate snapshot at entry time, so historical invoices are unaffected by later rate changes.';

CREATE INDEX IF NOT EXISTS ix_entries_timesheet ON public.timesheet_entries (timesheet_id, entry_date);
CREATE INDEX IF NOT EXISTS ix_entries_date      ON public.timesheet_entries (entry_date);
CREATE INDEX IF NOT EXISTS ix_entries_unconfirmed
  ON public.timesheet_entries (timesheet_id) WHERE source = 'AI_IMPORT' AND confirmed_at IS NULL;

-- W5: compute hours and amount; enforce eligibility; forbid writes to locked sheets.
CREATE OR REPLACE FUNCTION app.compute_entry() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  v_status public.timesheet_status;
  -- locked_at is a timestamptz, not a boolean. Selecting it into a boolean
  -- variable raises "invalid input syntax for type boolean".
  v_locked_at timestamptz;
  v_rate   numeric(18,4);
  v_user   uuid;
  v_contract uuid;
  v_period_start date;
  v_period_end   date;
BEGIN
  SELECT t.status, t.contract_id, t.period_start, t.period_end, t.user_id, t.locked_at
    INTO v_status, v_contract, v_period_start, v_period_end, v_user, v_locked_at
    FROM public.timesheets t WHERE t.id = NEW.timesheet_id;

  -- W2: locked/approved sheets are read-only.
  IF v_status IN ('LOCKED','APPROVED','UNDER_REVIEW') THEN
    RAISE EXCEPTION 'timesheet is %; entries cannot be modified', v_status
      USING ERRCODE = 'check_violation';
  END IF;
  IF v_locked_at IS NOT NULL THEN
    RAISE EXCEPTION 'timesheet is locked; entries cannot be modified'
      USING ERRCODE = 'check_violation';
  END IF;

  -- Entry date must fall inside the declared period.
  IF NEW.entry_date < v_period_start OR NEW.entry_date > v_period_end THEN
    RAISE EXCEPTION 'entry date % is outside the timesheet period %..%',
      NEW.entry_date, v_period_start, v_period_end USING ERRCODE = 'check_violation';
  END IF;

  -- W1: work eligibility for that specific date.
  IF NOT app.can_record_time(v_user, v_contract, NEW.entry_date) THEN
    RAISE EXCEPTION 'no active assignment for user % on contract % covering %',
      v_user, v_contract, NEW.entry_date USING ERRCODE = 'check_violation';
  END IF;

  -- hours from clock times when provided, else the supplied value.
  IF NEW.start_time IS NOT NULL AND NEW.end_time IS NOT NULL THEN
    NEW.hours := round(
      (EXTRACT(EPOCH FROM (NEW.end_time - NEW.start_time)) / 3600.0)
      - (NEW.break_minutes / 60.0), 4);
  END IF;

  IF NEW.hours < 0 THEN
    RAISE EXCEPTION 'computed hours must not be negative' USING ERRCODE = 'check_violation';
  END IF;

  -- rate snapshot from the assignment, overridable only by a rate-reading holder.
  IF NEW.rate_applied IS NULL THEN
    SELECT hourly_rate INTO v_rate FROM public.assignments
     WHERE id = (SELECT assignment_id FROM public.timesheets WHERE id = NEW.timesheet_id);
    NEW.rate_applied := v_rate;
  ELSIF app.current_user_id() IS NOT NULL THEN
    IF NOT EXISTS (SELECT 1 FROM public.timesheets t
                    JOIN public.companies c ON c.id = t.company_id
                   WHERE t.id = NEW.timesheet_id
                     AND (app.has_permission(t.company_id, 'timesheets.read_rate')
                          OR t.user_id = app.current_user_id()))
       THEN
      RAISE EXCEPTION 'insufficient permission to override rate'
        USING ERRCODE = 'insufficient_privilege';
    END IF;
  END IF;

  NEW.amount := CASE WHEN NEW.is_billable
                     THEN round(NEW.hours * COALESCE(NEW.rate_applied, 0), 4)
                     ELSE 0 END;

  -- A zero-hour entry is almost always an upstream bug (a timezone mistake, a
  -- missing rate, a client sending only the description). Refuse it rather than
  -- silently creating a 0h billable record.
  IF NEW.hours = 0 THEN
    RAISE EXCEPTION
      'timesheet entry for % computed 0 hours; check start/end times, break and rate',
      NEW.entry_date
      USING ERRCODE = 'check_violation',
            HINT = 'Provide start_time and end_time, or an explicit non-zero hours value.';
  END IF;

  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_entries_compute ON public.timesheet_entries;
CREATE TRIGGER trg_entries_compute BEFORE INSERT OR UPDATE ON public.timesheet_entries
  FOR EACH ROW EXECUTE FUNCTION app.compute_entry();

-- Recompute timesheet rollups after any entry change.
CREATE OR REPLACE FUNCTION app.refresh_timesheet_totals() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, app, pg_temp
AS $$
DECLARE v_ts uuid := COALESCE(NEW.timesheet_id, OLD.timesheet_id);
BEGIN
  UPDATE public.timesheets t SET
    total_hours = COALESCE(a.total, 0),
    billable_hours = COALESCE(a.billable, 0),
    total_amount = COALESCE(a.amount, 0),
    entry_count = COALESCE(a.cnt, 0)
    FROM (
      SELECT sum(e.hours) AS total,
             sum(e.hours) FILTER (WHERE e.is_billable) AS billable,
             sum(e.amount) AS amount,
             count(*) AS cnt
        FROM public.timesheet_entries e WHERE e.timesheet_id = v_ts
    ) a
   WHERE t.id = v_ts;
  RETURN NULL;
END
$$;

DROP TRIGGER IF EXISTS trg_entries_totals ON public.timesheet_entries;
CREATE TRIGGER trg_entries_totals AFTER INSERT OR UPDATE OR DELETE ON public.timesheet_entries
  FOR EACH ROW EXECUTE FUNCTION app.refresh_timesheet_totals();

-- -----------------------------------------------------------------------------
-- timesheet_revisions — full prior-state capture before every mutation (W3).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.timesheet_revisions (
  id           uuid PRIMARY KEY DEFAULT app.uuid7(),
  timesheet_id uuid NOT NULL REFERENCES public.timesheets(id) ON DELETE CASCADE,
  revision_no  int NOT NULL CHECK (revision_no > 0),
  changed_by   uuid REFERENCES public.users(id) ON DELETE SET NULL,
  change_type  text NOT NULL
                 CHECK (change_type IN ('CREATED','ENTRY_ADDED','ENTRY_UPDATED','ENTRY_REMOVED',
                                        'SUBMITTED','APPROVED','REJECTED','LOCKED','ADJUSTED','AI_IMPORT_APPLIED')),
  summary      text,
  snapshot     jsonb NOT NULL DEFAULT '{}'::jsonb,   -- complete pre-change state
  diff         jsonb,
  reason       text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (timesheet_id, revision_no)
);

CREATE INDEX IF NOT EXISTS ix_revisions_timesheet ON public.timesheet_revisions (timesheet_id, revision_no DESC);

CREATE OR REPLACE FUNCTION app.next_revision_no(p_timesheet_id uuid) RETURNS int
LANGUAGE sql AS $$
  SELECT COALESCE(max(revision_no), 0) + 1 FROM public.timesheet_revisions WHERE timesheet_id = p_timesheet_id;
$$;

-- Snapshot revisions are append-only.
DROP TRIGGER IF EXISTS trg_revisions_immutable ON public.timesheet_revisions;
CREATE TRIGGER trg_revisions_immutable BEFORE UPDATE OR DELETE ON public.timesheet_revisions
  FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

-- -----------------------------------------------------------------------------
-- timesheet_approvals — the multi-hop chain
-- (Employee D -> D manager -> B manager -> A manager)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.timesheet_approvals (
  id            uuid PRIMARY KEY DEFAULT app.uuid7(),
  timesheet_id  uuid NOT NULL REFERENCES public.timesheets(id) ON DELETE CASCADE,
  step_no       int NOT NULL CHECK (step_no > 0),
  company_id    uuid NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
  approver_user_id uuid REFERENCES public.users(id) ON DELETE SET NULL,
  required_permission text NOT NULL DEFAULT 'timesheets.approve',
  status        text NOT NULL DEFAULT 'PENDING'
                  CHECK (status IN ('PENDING','APPROVED','REJECTED','SKIPPED')),
  notes         text,
  requested_at  timestamptz NOT NULL DEFAULT now(),
  decided_at    timestamptz,
  due_at        timestamptz,
  UNIQUE (timesheet_id, step_no)
);

CREATE INDEX IF NOT EXISTS ix_tsapprovals_pending ON public.timesheet_approvals (approver_user_id, requested_at)
  WHERE status = 'PENDING';
CREATE INDEX IF NOT EXISTS ix_tsapprovals_company
  ON public.timesheet_approvals (company_id, status, requested_at);

-- Approval decisions: permission required, self-approval blocked, order enforced.
CREATE OR REPLACE FUNCTION app.assert_timesheet_approval() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  v_company uuid;
  v_user    uuid;
  v_sod     boolean;
  v_prior_pending int;
BEGIN
  -- Migrations, seeds and background workers have no end-user identity.
  -- mytrakin_api is neither superuser nor BYPASSRLS, so request traffic
  -- always falls through to the real checks below.
  IF app.is_trusted_context() THEN
    RETURN NEW;
  END IF;

  IF TG_OP = 'UPDATE' AND OLD.status = 'PENDING' AND NEW.status IN ('APPROVED','REJECTED') THEN
    SELECT t.company_id, t.user_id INTO v_company, v_user
      FROM public.timesheets t WHERE t.id = NEW.timesheet_id;
  -- Migrations, seeds and background workers have no end-user identity.
  -- mytrakin_api is neither superuser nor BYPASSRLS, so request traffic
  -- always falls through to the real checks below.
    SELECT COALESCE((c.settings->'segregation_of_duties'->>'allow_self_approval'), 'false')::boolean
      INTO v_sod FROM public.companies c WHERE c.id = v_company;

    IF NEW.approver_user_id IS NOT NULL
       AND NEW.approver_user_id = v_user AND NOT v_sod THEN
      RAISE EXCEPTION 'segregation of duties: cannot approve your own timesheet'
        USING ERRCODE = 'insufficient_privilege';
    END IF;

    IF NEW.approver_user_id IS NOT NULL AND NEW.approver_user_id <> app.current_user_id()
       AND app.current_user_id() IS NOT NULL THEN
      RAISE EXCEPTION 'only the designated approver may decide this step'
        USING ERRCODE = 'insufficient_privilege';
    END IF;

    IF NOT app.has_permission(v_company, NEW.required_permission) THEN
      RAISE EXCEPTION 'missing permission % to decide this approval', NEW.required_permission
        USING ERRCODE = 'insufficient_privilege';
    END IF;

    -- Steps must be decided in order.
    IF NEW.status = 'APPROVED' THEN
      SELECT count(*) INTO v_prior_pending FROM public.timesheet_approvals
       WHERE timesheet_id = NEW.timesheet_id AND step_no < NEW.step_no AND status = 'PENDING';
      IF v_prior_pending > 0 THEN
        RAISE EXCEPTION 'approval step % cannot be decided before earlier pending steps', NEW.step_no
          USING ERRCODE = 'check_violation';
      END IF;
    END IF;

    NEW.decided_at := now();
  END IF;
  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_tsapprovals_guard ON public.timesheet_approvals;
CREATE TRIGGER trg_tsapprovals_guard BEFORE UPDATE ON public.timesheet_approvals
  FOR EACH ROW EXECUTE FUNCTION app.assert_timesheet_approval();

-- Seed the approval chain from the contract when a timesheet is submitted.
CREATE OR REPLACE FUNCTION app.build_approval_chain() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, app, pg_temp
AS $$
DECLARE
  v_chain jsonb;
  v_step  jsonb;
  v_no    int := 0;
BEGIN
  IF NEW.status = 'SUBMITTED' AND OLD.status <> 'SUBMITTED' THEN
    SELECT c.timesheet_approval_chain INTO v_chain
      FROM public.contracts c WHERE c.id = NEW.contract_id;

    IF v_chain IS NOT NULL AND jsonb_typeof(v_chain->'steps') = 'array' THEN
      -- clear any prior chain (re-submission after rejection)
      DELETE FROM public.timesheet_approvals WHERE timesheet_id = NEW.id;

      FOR v_step IN SELECT * FROM jsonb_array_elements(v_chain->'steps')
      LOOP
        v_no := v_no + 1;
        INSERT INTO public.timesheet_approvals (
          timesheet_id, step_no, company_id, approver_user_id, required_permission, due_at
        ) VALUES (
          NEW.id, v_no,
          COALESCE((v_step->>'company_id')::uuid, NEW.company_id),
          NULLIF(v_step->>'user_id', '')::uuid,
          COALESCE(v_step->>'required_permission', 'timesheets.approve'),
          CURRENT_DATE + COALESCE((v_step->>'due_within_days')::int, 3)
        );
      END LOOP;

      UPDATE public.timesheets SET status = 'UNDER_REVIEW', current_step = 1
       WHERE id = NEW.id AND EXISTS (SELECT 1 FROM public.timesheet_approvals
                                        WHERE timesheet_id = NEW.id);
    END IF;
  END IF;
  RETURN NULL;
END
$$;

DROP TRIGGER IF EXISTS trg_timesheets_chain ON public.timesheets;
CREATE TRIGGER trg_timesheets_chain AFTER UPDATE OF status ON public.timesheets
  FOR EACH ROW EXECUTE FUNCTION app.build_approval_chain();

-- Final approval advances the sheet to APPROVED.
CREATE OR REPLACE FUNCTION app.advance_timesheet_on_final_approval() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, app, pg_temp
AS $$
DECLARE v_remaining int;
BEGIN
  IF NEW.status = 'APPROVED' THEN
    SELECT count(*) INTO v_remaining FROM public.timesheet_approvals
     WHERE timesheet_id = NEW.timesheet_id AND status = 'PENDING';
    IF v_remaining = 0 THEN
      UPDATE public.timesheets SET status = 'APPROVED', approved_at = now()
       WHERE id = NEW.timesheet_id AND status IN ('SUBMITTED','UNDER_REVIEW');
    ELSE
      UPDATE public.timesheets SET current_step = NEW.step_no + 1
       WHERE id = NEW.timesheet_id AND status = 'UNDER_REVIEW';
    END IF;
  END IF;

  IF NEW.status = 'REJECTED' THEN
    UPDATE public.timesheets SET status = 'REJECTED', rejection_reason = NEW.notes
     WHERE id = NEW.timesheet_id AND status IN ('SUBMITTED','UNDER_REVIEW');
  END IF;
  RETURN NULL;
END
$$;

DROP TRIGGER IF EXISTS trg_tsapprovals_advance ON public.timesheet_approvals;
CREATE TRIGGER trg_tsapprovals_advance AFTER UPDATE ON public.timesheet_approvals
  FOR EACH ROW WHEN (NEW.status IN ('APPROVED','REJECTED') AND OLD.status = 'PENDING')
  EXECUTE FUNCTION app.advance_timesheet_on_final_approval();

-- Approval emits an event so billing can be triggered by the worker, not inline.
CREATE OR REPLACE FUNCTION app.emit_timesheet_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, app, platform, pg_temp
AS $$
BEGIN
  IF NEW.status IS DISTINCT FROM OLD.status THEN
    INSERT INTO platform.outbox_events (
      event_type, company_id, actor_user_id, aggregate_type, aggregate_id, payload, idempotency_key
    ) VALUES (
      'TIMESHEET_' || NEW.status, NEW.company_id, app.current_user_id(), 'timesheet', NEW.id,
      jsonb_build_object(
        'public_id', NEW.public_id, 'user_id', NEW.user_id, 'contract_id', NEW.contract_id,
        'project_id', NEW.project_id, 'period_start', NEW.period_start, 'period_end', NEW.period_end,
        'billable_hours', NEW.billable_hours, 'total_amount', NEW.total_amount, 'currency', NEW.currency),
      'timesheet_status:' || NEW.id || ':' || NEW.status || ':' || NEW.updated_at
    );
  END IF;
  RETURN NULL;
END
$$;

DROP TRIGGER IF EXISTS trg_timesheets_event ON public.timesheets;
CREATE TRIGGER trg_timesheets_event AFTER UPDATE ON public.timesheets
  FOR EACH ROW EXECUTE FUNCTION app.emit_timesheet_event();

-- =============================================================================
-- Leave management
-- Entitlements are policy-driven (company or contract), not hard-coded.
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.leave_policies (
  id                 uuid PRIMARY KEY DEFAULT app.uuid7(),
  company_id         uuid NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
  public_id          text NOT NULL UNIQUE,
  name               text NOT NULL,
  leave_type         text NOT NULL
                       CHECK (leave_type IN ('ANNUAL','SICK','CASUAL','PARENTAL','UNPAID','BEREAVEMENT','COMP_OFF','OTHER')),
  accrual_method     text NOT NULL DEFAULT 'MONTHLY'
                       CHECK (accrual_method IN ('MONTHLY','ANNUAL','PER_PERIOD','NONE')),
  accrual_rate       numeric(8,4) NOT NULL DEFAULT 0 CHECK (accrual_rate >= 0),
  max_balance        numeric(8,2),
  carry_forward_limit numeric(8,2),
  requires_approval  boolean NOT NULL DEFAULT true,
  approval_role_id   uuid REFERENCES public.company_roles(id) ON DELETE SET NULL,
  min_notice_days    int NOT NULL DEFAULT 0 CHECK (min_notice_days >= 0),
  max_consecutive_days int CHECK (max_consecutive_days IS NULL OR max_consecutive_days > 0),
  allow_negative_balance boolean NOT NULL DEFAULT false,
  effective_from     date NOT NULL DEFAULT current_date,
  effective_to       date,
  is_active          boolean NOT NULL DEFAULT true,
  created_by         uuid REFERENCES public.users(id) ON DELETE SET NULL,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_leave_policy_dates CHECK (effective_to IS NULL OR effective_to >= effective_from)
);

CREATE INDEX IF NOT EXISTS ix_leave_policies_company
  ON public.leave_policies (company_id, leave_type) WHERE is_active;

CREATE TABLE IF NOT EXISTS public.leave_balances (
  id            uuid PRIMARY KEY DEFAULT app.uuid7(),
  user_id       uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  company_id    uuid NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
  leave_policy_id uuid NOT NULL REFERENCES public.leave_policies(id) ON DELETE CASCADE,
  year          int NOT NULL,
  entitled      numeric(8,2) NOT NULL DEFAULT 0,
  accrued       numeric(8,2) NOT NULL DEFAULT 0,
  taken         numeric(8,2) NOT NULL DEFAULT 0,
  pending       numeric(8,2) NOT NULL DEFAULT 0,
  carried_over  numeric(8,2) NOT NULL DEFAULT 0,
  expires_on    date,
  updated_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, company_id, leave_policy_id, year),
  CONSTRAINT ck_leave_balances_nonneg CHECK (entitled >= 0 AND accrued >= 0 AND taken >= 0 AND pending >= 0)
);

COMMENT ON COLUMN public.leave_balances.accrued IS
  'Derived ledger. available = accrued + carried_over - taken - pending, computed by app.leave_available().';

CREATE OR REPLACE FUNCTION app.leave_available(
  p_user_id uuid, p_company_id uuid, p_leave_policy_id uuid, p_year int
) RETURNS numeric
LANGUAGE sql STABLE AS $$
  SELECT COALESCE(accrued, 0) + COALESCE(carried_over, 0) - COALESCE(taken, 0) - COALESCE(pending, 0)
    FROM public.leave_balances
   WHERE user_id = p_user_id AND company_id = p_company_id
     AND leave_policy_id = p_leave_policy_id AND year = p_year;
$$;

CREATE TABLE IF NOT EXISTS public.leave_requests (
  id              uuid PRIMARY KEY DEFAULT app.uuid7(),
  public_id       text NOT NULL UNIQUE,
  user_id         uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  company_id      uuid NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
  leave_policy_id uuid NOT NULL REFERENCES public.leave_policies(id) ON DELETE RESTRICT,
  start_date      date NOT NULL,
  end_date        date NOT NULL,
  total_days      numeric(6,2) NOT NULL CHECK (total_days > 0),
  reason          text,
  status          public.leave_status NOT NULL DEFAULT 'DRAFT',
  approver_user_id uuid REFERENCES public.users(id) ON DELETE SET NULL,
  decided_at      timestamptz,
  decision_notes  text,
  attachment_document_id uuid REFERENCES public.documents(id) ON DELETE SET NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_leave_dates CHECK (end_date >= start_date)
);

CREATE OR REPLACE FUNCTION app.assign_leave_public_id() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_try int := 0; v_id text;
BEGIN

  LOOP
    v_id := app.gen_public_id('LR', 8);
    EXIT WHEN NOT EXISTS (SELECT 1 FROM public.leave_requests WHERE public_id = v_id);
    v_try := v_try + 1;
    IF v_try > 5 THEN RAISE EXCEPTION 'could not allocate leave public_id'; END IF;
  END LOOP;
  NEW.public_id := v_id;
  RETURN NEW;
END
$$;

SELECT app.attach_triggers('public.leave_requests'::regclass);
DROP TRIGGER IF EXISTS trg_leave_public_id ON public.leave_requests;
CREATE TRIGGER trg_leave_public_id BEFORE INSERT ON public.leave_requests
  FOR EACH ROW WHEN (NEW.public_id IS NULL)
  EXECUTE FUNCTION app.assign_leave_public_id();

CREATE INDEX IF NOT EXISTS ix_leave_requests_user    ON public.leave_requests (user_id, start_date DESC);
CREATE INDEX IF NOT EXISTS ix_leave_requests_company ON public.leave_requests (company_id, status, start_date DESC);
CREATE INDEX IF NOT EXISTS ix_leave_requests_pending ON public.leave_requests (company_id, created_at)
  WHERE status = 'PENDING';

-- Balance checks and the leave state machine.
CREATE OR REPLACE FUNCTION app.assert_leave_request() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  v_policy public.leave_policies%ROWTYPE;
  v_available numeric;
BEGIN
  -- Migrations, seeds and background workers have no end-user identity.
  -- mytrakin_api is neither superuser nor BYPASSRLS, so request traffic
  -- always falls through to the real checks below.
  IF app.is_trusted_context() THEN
    RETURN NEW;
  END IF;

  SELECT * INTO v_policy FROM public.leave_policies WHERE id = NEW.leave_policy_id;
  -- Migrations, seeds and background workers have no end-user identity.
  -- mytrakin_api is neither superuser nor BYPASSRLS, so request traffic
  -- always falls through to the real checks below.
  IF v_policy.company_id <> NEW.company_id THEN
    RAISE EXCEPTION 'leave policy does not belong to this company' USING ERRCODE = 'check_violation';
  END IF;

  IF app.current_user_id() IS NOT NULL AND NEW.user_id <> app.current_user_id()
     AND NOT app.has_permission(NEW.company_id, 'leave.approve') THEN
    RAISE EXCEPTION 'cannot file a leave request for another user'
      USING ERRCODE = 'insufficient_privilege';
  END IF;

  IF v_policy.min_notice_days > 0
     AND NEW.start_date < current_date + v_policy.min_notice_days THEN
    RAISE EXCEPTION 'policy requires % day(s) notice', v_policy.min_notice_days
      USING ERRCODE = 'check_violation';
  END IF;

  IF v_policy.max_consecutive_days IS NOT NULL AND NEW.total_days > v_policy.max_consecutive_days THEN
    RAISE EXCEPTION 'policy caps consecutive leave at % days', v_policy.max_consecutive_days
      USING ERRCODE = 'check_violation';
  END IF;

  IF NOT v_policy.allow_negative_balance THEN
    v_available := app.leave_available(NEW.user_id, NEW.company_id, NEW.leave_policy_id,
                                       EXTRACT(YEAR FROM NEW.start_date)::int);
    IF v_available IS NOT NULL AND NEW.total_days > v_available THEN
      RAISE EXCEPTION 'insufficient leave balance: requested %, available %',
        NEW.total_days, v_available USING ERRCODE = 'check_violation';
    END IF;
  END IF;

  -- No overlapping approved/pending request for the same person.
  IF EXISTS (
    SELECT 1 FROM public.leave_requests r
     WHERE r.user_id = NEW.user_id AND r.id <> NEW.id
       AND r.status IN ('PENDING','APPROVED')
       AND r.start_date <= NEW.end_date AND r.end_date >= NEW.start_date
  ) THEN
    RAISE EXCEPTION 'overlapping leave request exists for this period' USING ERRCODE = 'check_violation';
  END IF;

  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_leave_guard ON public.leave_requests;
CREATE TRIGGER trg_leave_guard BEFORE INSERT OR UPDATE OF start_date, end_date, total_days, leave_policy_id
  ON public.leave_requests FOR EACH ROW EXECUTE FUNCTION app.assert_leave_request();

CREATE OR REPLACE FUNCTION app.apply_leave_decision() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_year int;
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

  IF NEW.status IS DISTINCT FROM OLD.status AND NEW.status IN ('APPROVED','REJECTED') THEN
    IF NEW.status = 'APPROVED' AND NOT app.has_permission(NEW.company_id, 'leave.approve') THEN
      RAISE EXCEPTION 'missing permission leave.approve' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.approver_user_id = NEW.user_id THEN
      RAISE EXCEPTION 'segregation of duties: cannot approve your own leave request'
        USING ERRCODE = 'insufficient_privilege';
    END IF;

    NEW.decided_at := now();

    IF NEW.status = 'APPROVED' THEN
      v_year := EXTRACT(YEAR FROM NEW.start_date)::int;
      INSERT INTO public.leave_balances (user_id, company_id, leave_policy_id, year, taken)
      VALUES (NEW.user_id, NEW.company_id, NEW.leave_policy_id, v_year, NEW.total_days)
      ON CONFLICT (user_id, company_id, leave_policy_id, year)
      DO UPDATE SET taken = public.leave_balances.taken + EXCLUDED.taken,
                    pending = GREATEST(public.leave_balances.pending - NEW.total_days, 0),
                    updated_at = now();
    END IF;
  END IF;
  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_leave_decision ON public.leave_requests;
CREATE TRIGGER trg_leave_decision BEFORE UPDATE ON public.leave_requests
  FOR EACH ROW EXECUTE FUNCTION app.apply_leave_decision();

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['timesheet_entries','leave_policies','leave_balances'] LOOP
    PERFORM app.attach_updated_at(
      format('public.%I', t)::regclass,
      'trg_' || t || '_updated_at');
  END LOOP;
END
$$;

GRANT EXECUTE ON FUNCTION app.can_record_time(uuid, uuid, date) TO mytrakin_api, mytrakin_worker;
GRANT EXECUTE ON FUNCTION app.leave_available(uuid, uuid, uuid, int) TO mytrakin_api, mytrakin_worker;

GRANT SELECT, INSERT, UPDATE, DELETE ON
  public.assignments, public.timesheets, public.timesheet_entries,
  public.timesheet_revisions, public.timesheet_approvals,
  public.leave_policies, public.leave_balances, public.leave_requests
TO mytrakin_api, mytrakin_worker;
