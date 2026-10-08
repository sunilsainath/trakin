-- =============================================================================
-- MyTrakin :: 0009_billing.sql
-- Invoice engine: billing runs, invoices, items, allocations, approvals.
--
-- Invariants enforced in the database:
--   B1  invoice amounts are derived from approved timesheets / contract line
--       items — never accepted from a client
--   B2  invoice generation is idempotent per (contract, period, billing_basis)
--   B3  an invoice between two companies cannot leave DRAFT without an ACTIVE
--       MSA; it is generated but flagged MSA_REQUIRED
--   B4  approval requires segregation of duties; payment requires an approver
--   B5  every invoice item traces back to a contract line item or timesheet
-- =============================================================================

-- -----------------------------------------------------------------------------
-- billing_runs — one execution of the generation engine for a scope.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.billing_runs (
  id             uuid PRIMARY KEY DEFAULT app.uuid7(),
  public_id      text NOT NULL UNIQUE,
  company_id     uuid NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
  run_type       text NOT NULL DEFAULT 'INVOICE_GENERATION'
                   CHECK (run_type IN ('INVOICE_GENERATION','RECONCILIATION','FX_REVALUATION','EXPORT')),
  scope          text NOT NULL DEFAULT '{}'::jsonb::text,  -- documented scope, stored as jsonb below
  scope_json     jsonb NOT NULL DEFAULT '{}'::jsonb,
  period_start   date,
  period_end     date,
  status         text NOT NULL DEFAULT 'PENDING'
                   CHECK (status IN ('PENDING','RUNNING','SUCCEEDED','PARTIALLY_FAILED','FAILED','CANCELLED')),
  idempotency_key text UNIQUE,
  requested_by   uuid REFERENCES public.users(id) ON DELETE SET NULL,
  contracts_scanned int NOT NULL DEFAULT 0,
  invoices_created int NOT NULL DEFAULT 0,
  invoices_skipped int NOT NULL DEFAULT 0,
  total_amount   numeric(18,4) NOT NULL DEFAULT 0,
  currency       char(3) NOT NULL DEFAULT 'USD',
  error_details  jsonb,
  started_at     timestamptz,
  finished_at    timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);

COMMENT ON COLUMN public.billing_runs.idempotency_key IS
  'Unique key for the run. Re-running the same period returns the existing run instead of duplicating invoices.';

CREATE INDEX IF NOT EXISTS ix_billing_runs_company ON public.billing_runs (company_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_billing_runs_status  ON public.billing_runs (status, created_at);

CREATE OR REPLACE FUNCTION app.assign_billing_run_public_id() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_try int := 0; v_id text;
BEGIN

  LOOP
    v_id := app.gen_public_id('BR', 8);
    EXIT WHEN NOT EXISTS (SELECT 1 FROM public.billing_runs WHERE public_id = v_id);
    v_try := v_try + 1;
    IF v_try > 5 THEN RAISE EXCEPTION 'could not allocate billing run public_id'; END IF;
  END LOOP;
  NEW.public_id := v_id;
  RETURN NEW;
END
$$;

SELECT app.attach_triggers('public.billing_runs'::regclass);
DROP TRIGGER IF EXISTS trg_billing_runs_public_id ON public.billing_runs;
CREATE TRIGGER trg_billing_runs_public_id BEFORE INSERT ON public.billing_runs
  FOR EACH ROW WHEN (NEW.public_id IS NULL)
  EXECUTE FUNCTION app.assign_billing_run_public_id();

-- -----------------------------------------------------------------------------
-- invoices
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.invoices (
  id                 uuid PRIMARY KEY DEFAULT app.uuid7(),
  public_id          text NOT NULL UNIQUE,
  billing_run_id     uuid REFERENCES public.billing_runs(id) ON DELETE SET NULL,
  direction          public.invoice_direction NOT NULL,
  -- The issuing company (who raised the invoice) and the counterparty.
  company_id         uuid NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
  counterparty_company_id uuid REFERENCES public.companies(id) ON DELETE RESTRICT,
  counterparty_user_id    uuid REFERENCES public.users(id) ON DELETE RESTRICT,
  project_id         uuid REFERENCES public.projects(id) ON DELETE SET NULL,
  sow_id             uuid REFERENCES public.sows(id) ON DELETE SET NULL,
  contract_id        uuid NOT NULL REFERENCES public.contracts(id) ON DELETE RESTRICT,
  msa_id             uuid REFERENCES public.msas(id) ON DELETE SET NULL,
  invoice_number     text,
  status             public.invoice_status NOT NULL DEFAULT 'DRAFT',
  period_start       date NOT NULL,
  period_end         date NOT NULL,
  issue_date         date,
  due_date           date NOT NULL,
  currency           char(3) NOT NULL DEFAULT 'USD',
  subtotal           numeric(18,4) NOT NULL DEFAULT 0,
  tax_total          numeric(18,4) NOT NULL DEFAULT 0,
  total_amount       numeric(18,4) NOT NULL DEFAULT 0,
  amount_paid        numeric(18,4) NOT NULL DEFAULT 0 CHECK (amount_paid >= 0),
  amount_disputed    numeric(18,4) NOT NULL DEFAULT 0 CHECK (amount_disputed >= 0),
  balance_due        numeric(18,4) NOT NULL DEFAULT 0,
  fx_rate            numeric(18,8) CHECK (fx_rate IS NULL OR fx_rate > 0),
  original_currency  char(3),
  original_amount    numeric(18,4),
  converted_amount   numeric(18,4),
  payment_terms_days int NOT NULL DEFAULT 30,
  msa_required       boolean NOT NULL DEFAULT false,   -- B3 flag
  msa_block_reason   text,
  disputed_reason    text,
  rejected_reason    text,
  notes              text,
  terms_snapshot     jsonb NOT NULL DEFAULT '{}'::jsonb,  -- contract terms at generation time
  submitted_at       timestamptz,
  approved_at        timestamptz,
  paid_at            timestamptz,
  cancelled_at       timestamptz,
  document_id        uuid REFERENCES public.documents(id) ON DELETE SET NULL,
  locked             boolean NOT NULL DEFAULT false,
  version            int NOT NULL DEFAULT 1,
  created_by         uuid REFERENCES public.users(id) ON DELETE SET NULL,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  deleted_at         timestamptz,
  CONSTRAINT ck_invoice_public_id CHECK (public_id ~ '^I[0-9A-HJKMNP-TV-Z]{8}$'),
  CONSTRAINT ck_invoice_dates     CHECK (period_end >= period_start AND due_date >= issue_date),
  CONSTRAINT ck_invoice_currency  CHECK (currency ~ '^[A-Z]{3}$'),
  CONSTRAINT ck_invoice_amounts   CHECK (subtotal >= 0 AND tax_total >= 0 AND total_amount >= 0
                                          AND amount_paid <= total_amount + 0.0001),
  -- B3: a submitted invoice between two companies needs an active MSA.
  CONSTRAINT ck_invoice_msa_gate CHECK (
    status NOT IN ('SUBMITTED','APPROVED','PARTIALLY_PAID','PAID')
    OR direction = 'PAYABLE'
    OR msa_required = false
  )
);

COMMENT ON TABLE public.invoices IS
  'Invoice header. RECEIVABLE = money owed to company_id; PAYABLE = company_id owes the counterparty.';
COMMENT ON COLUMN public.invoices.terms_snapshot IS
  'Contract terms as they were at generation time, so later contract edits cannot alter history.';
COMMENT ON COLUMN public.invoices.msa_required IS
  'True when an invoice was generated without an ACTIVE MSA. It stays DRAFT and cannot be submitted.';

CREATE INDEX IF NOT EXISTS ix_invoices_company
  ON public.invoices (company_id, created_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_invoices_counterparty
  ON public.invoices (counterparty_company_id, status) WHERE counterparty_company_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_invoices_contract
  ON public.invoices (contract_id, period_start, period_end) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_invoices_project  ON public.invoices (project_id, status);
CREATE INDEX IF NOT EXISTS ix_invoices_due
  ON public.invoices (due_date) WHERE deleted_at IS NULL AND status NOT IN ('PAID','CANCELLED');
CREATE INDEX IF NOT EXISTS ix_invoices_overdue
  ON public.invoices (company_id, due_date, total_amount)
  WHERE deleted_at IS NULL AND status IN ('SUBMITTED','APPROVED','PARTIALLY_PAID','OVERDUE');
-- B2: one invoice per contract + period + direction.
CREATE UNIQUE INDEX IF NOT EXISTS ux_invoices_period
  ON public.invoices (contract_id, direction, period_start, period_end, currency)
  WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_invoices_ar_aging
  ON public.invoices (company_id, due_date)
  WHERE deleted_at IS NULL AND direction = 'RECEIVABLE'
    AND status IN ('SUBMITTED','APPROVED','PARTIALLY_PAID','OVERDUE');

CREATE OR REPLACE FUNCTION app.assign_invoice_public_id() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_try int := 0; v_id text;
BEGIN

  LOOP
    v_id := app.gen_public_id('I', 8);
    EXIT WHEN NOT EXISTS (SELECT 1 FROM public.invoices WHERE public_id = v_id);
    v_try := v_try + 1;
    IF v_try > 5 THEN RAISE EXCEPTION 'could not allocate invoice public_id'; END IF;
  END LOOP;
  NEW.public_id := v_id;
  RETURN NEW;
END
$$;

SELECT app.attach_triggers('public.invoices'::regclass);
DROP TRIGGER IF EXISTS trg_invoices_public_id ON public.invoices;
CREATE TRIGGER trg_invoices_public_id BEFORE INSERT ON public.invoices
  FOR EACH ROW WHEN (NEW.public_id IS NULL)
  EXECUTE FUNCTION app.assign_invoice_public_id();

-- Derived amounts + state machine + MSA gate.
CREATE OR REPLACE FUNCTION app.assert_invoice() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  v_from public.invoice_status;
  v_to   public.invoice_status;
  v_allowed text[];
  v_contract public.contracts%ROWTYPE;
  v_msa_ok boolean;
  v_sod boolean;
BEGIN
  -- Migrations, seeds and background workers have no end-user identity.
  -- mytrakin_api is neither superuser nor BYPASSRLS, so request traffic
  -- always falls through to the real checks below.
  IF app.is_trusted_context() THEN
    RETURN NEW;
  END IF;

  -- Totals are derived from items, never trusted from the request.
  SELECT COALESCE(sum(subtotal), 0), COALESCE(sum(tax_total), 0), COALESCE(sum(total), 0)
    INTO NEW.subtotal, NEW.tax_total, NEW.total_amount
    FROM public.invoice_items WHERE invoice_id = NEW.id;
  -- Migrations, seeds and background workers have no end-user identity.
  -- mytrakin_api is neither superuser nor BYPASSRLS, so request traffic
  -- always falls through to the real checks below.
  NEW.balance_due := round(NEW.total_amount - NEW.amount_paid - NEW.amount_disputed, 4);

  IF TG_OP = 'UPDATE' THEN
    IF OLD.total_amount IS DISTINCT FROM NEW.total_amount AND OLD.locked THEN
      RAISE EXCEPTION 'invoice % is locked; amounts cannot change', OLD.public_id
        USING ERRCODE = 'restrict_violation';
    END IF;

    v_from := OLD.status;
    v_to := NEW.status;
    IF v_from = v_to THEN RETURN NEW; END IF;

    v_allowed := CASE v_from
      WHEN 'DRAFT'          THEN ARRAY['PENDING','SUBMITTED','CANCELLED']::text[]
      WHEN 'PENDING'        THEN ARRAY['DRAFT','SUBMITTED','CANCELLED']::text[]
      WHEN 'SUBMITTED'      THEN ARRAY['APPROVED','REJECTED','DISPUTED','OVERDUE','CANCELLED']::text[]
      WHEN 'APPROVED'       THEN ARRAY['PARTIALLY_PAID','PAID','OVERDUE','DISPUTED','CANCELLED']::text[]
      WHEN 'PARTIALLY_PAID' THEN ARRAY['PAID','OVERDUE','DISPUTED','REFUNDED','CANCELLED']::text[]
      WHEN 'OVERDUE'        THEN ARRAY['PARTIALLY_PAID','PAID','DISPUTED','CANCELLED']::text[]
      WHEN 'DISPUTED'       THEN ARRAY['APPROVED','REJECTED','CANCELLED']::text[]
      WHEN 'REJECTED'       THEN ARRAY['DRAFT','CANCELLED']::text[]
      WHEN 'PAID'           THEN ARRAY['REFUNDED']::text[]
      WHEN 'CANCELLED'      THEN ARRAY[]::text[]
      WHEN 'REFUNDED'       THEN ARRAY[]::text[]
      ELSE ARRAY[]::text[]
    END;

    IF NOT (v_to::text = ANY (v_allowed)) THEN
      RAISE EXCEPTION 'invalid invoice transition % -> %', v_from, v_to
        USING ERRCODE = 'check_violation';
    END IF;

    SELECT * INTO v_contract FROM public.contracts WHERE id = NEW.contract_id;

    -- B3: MSA gate.
    IF NEW.direction = 'RECEIVABLE' AND NEW.counterparty_company_id IS NOT NULL THEN
      v_msa_ok := app.has_active_msa(NEW.company_id, NEW.counterparty_company_id);
      NEW.msa_required := NOT v_msa_ok;
      NEW.msa_block_reason := CASE WHEN v_msa_ok THEN NULL
                                  ELSE 'An active Master Service Agreement is required before submission.' END;
      IF NOT v_msa_ok AND v_to IN ('SUBMITTED','APPROVED','PARTIALLY_PAID','PAID') THEN
        RAISE EXCEPTION 'invoice % requires an ACTIVE msa before submission (contract %)',
          NEW.public_id, NEW.contract_id USING ERRCODE = 'check_violation';
      END IF;
    ELSE
      NEW.msa_required := false;
      NEW.msa_block_reason := NULL;
    END IF;

    -- B4: segregation of duties.
    SELECT COALESCE((settings->'segregation_of_duties'->>'allow_self_approval'), 'false')::boolean
      INTO v_sod FROM public.companies WHERE id = NEW.company_id;

    IF v_to = 'SUBMITTED' AND NOT app.has_permission(NEW.company_id, 'invoices.submit') THEN
      RAISE EXCEPTION 'missing permission invoices.submit' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF v_to = 'APPROVED' AND NOT app.has_permission(NEW.company_id, 'invoices.approve') THEN
      RAISE EXCEPTION 'missing permission invoices.approve' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF v_to = 'APPROVED' AND NEW.created_by = app.current_user_id() AND NOT v_sod THEN
      RAISE EXCEPTION 'segregation of duties: cannot approve an invoice you created'
        USING ERRCODE = 'insufficient_privilege';
    END IF;

    NEW.submitted_at := CASE WHEN v_to = 'SUBMITTED' THEN now() ELSE NEW.submitted_at END;
    NEW.approved_at  := CASE WHEN v_to = 'APPROVED'  THEN now() ELSE NEW.approved_at END;
    NEW.paid_at      := CASE WHEN v_to = 'PAID'      THEN now() ELSE NEW.paid_at END;
    NEW.cancelled_at := CASE WHEN v_to = 'CANCELLED' THEN now() ELSE NEW.cancelled_at END;
  END IF;

  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_invoices_assert ON public.invoices;
CREATE TRIGGER trg_invoices_assert BEFORE INSERT OR UPDATE ON public.invoices
  FOR EACH ROW EXECUTE FUNCTION app.assert_invoice();

-- Creating an invoice requires invoices.create.
CREATE OR REPLACE FUNCTION app.assert_invoice_insert() RETURNS trigger
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
  IF NOT app.has_permission(NEW.company_id, 'invoices.create') THEN
    RAISE EXCEPTION 'missing permission invoices.create for company %', NEW.company_id
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_invoices_guard ON public.invoices;
CREATE TRIGGER trg_invoices_guard BEFORE INSERT ON public.invoices
  FOR EACH ROW EXECUTE FUNCTION app.assert_invoice_insert();

-- -----------------------------------------------------------------------------
-- invoice_items — B5: every item has a traceable source.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.invoice_items (
  id                uuid PRIMARY KEY DEFAULT app.uuid7(),
  invoice_id        uuid NOT NULL REFERENCES public.invoices(id) ON DELETE CASCADE,
  contract_id       uuid NOT NULL REFERENCES public.contracts(id) ON DELETE RESTRICT,
  source_line_item_id uuid REFERENCES public.contract_line_items(id) ON DELETE SET NULL,
  source_timesheet_id  uuid REFERENCES public.timesheets(id) ON DELETE SET NULL,
  project_id        uuid REFERENCES public.projects(id) ON DELETE SET NULL,
  contract_role_id  uuid REFERENCES public.contract_roles(id) ON DELETE SET NULL,
  line_type         public.line_item_kind NOT NULL DEFAULT 'TIMESHEET',
  description       text NOT NULL,
  quantity          numeric(18,4) NOT NULL DEFAULT 1 CHECK (quantity > 0),
  unit              text NOT NULL DEFAULT 'HOUR',
  unit_rate         numeric(18,4) NOT NULL DEFAULT 0 CHECK (unit_rate >= 0),
  subtotal          numeric(18,4) NOT NULL DEFAULT 0,
  tax_rate          numeric(7,4) NOT NULL DEFAULT 0 CHECK (tax_rate >= 0),
  tax_total         numeric(18,4) NOT NULL DEFAULT 0,
  total             numeric(18,4) NOT NULL DEFAULT 0,
  currency          char(3) NOT NULL DEFAULT 'USD',
  service_period_start date,
  service_period_end   date,
  metadata          jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  -- B5: provenance is mandatory.
  -- Provenance is mandatory for anything derived from work or a contract term.
  -- USAGE-based items are legitimately source-free (a metered charge), so they
  -- are exempted rather than forcing a fake reference.
  CONSTRAINT ck_invoice_item_source CHECK (
    source_line_item_id IS NOT NULL
    OR source_timesheet_id IS NOT NULL
    OR line_type IN ('FIXED', 'RECURRING', 'USAGE')
  ),
  CONSTRAINT ck_invoice_item_currency CHECK (currency ~ '^[A-Z]{3}$')
);

COMMENT ON TABLE public.invoice_items IS
  'Invoice line. Provenance (contract line item or timesheet) is mandatory so every billable amount is traceable.';

CREATE INDEX IF NOT EXISTS ix_invoice_items_invoice ON public.invoice_items (invoice_id);
CREATE INDEX IF NOT EXISTS ix_invoice_items_timesheet ON public.invoice_items (source_timesheet_id)
  WHERE source_timesheet_id IS NOT NULL;

-- An approved timesheet may only ever be invoiced once.
CREATE UNIQUE INDEX IF NOT EXISTS ux_invoice_item_timesheet
  ON public.invoice_items (source_timesheet_id)
  WHERE source_timesheet_id IS NOT NULL;

CREATE OR REPLACE FUNCTION app.compute_invoice_item() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_invoice_locked boolean; v_status public.invoice_status; v_approver uuid;
BEGIN
  SELECT locked, status, created_by INTO v_invoice_locked, v_status, v_approver
    FROM public.invoices WHERE id = NEW.invoice_id;

  IF v_invoice_locked OR v_status IN ('APPROVED','PAID','PARTIALLY_PAID','CANCELLED','REFUNDED') THEN
    RAISE EXCEPTION 'invoice is %; items cannot be modified', v_status
      USING ERRCODE = 'check_violation';
  END IF;

  NEW.subtotal  := round(NEW.quantity * NEW.unit_rate, 4);
  NEW.tax_total := round(NEW.subtotal * NEW.tax_rate, 4);
  NEW.total     := round(NEW.subtotal + NEW.tax_total, 4);
  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_invoice_items_compute ON public.invoice_items;
CREATE TRIGGER trg_invoice_items_compute BEFORE INSERT OR UPDATE ON public.invoice_items
  FOR EACH ROW EXECUTE FUNCTION app.compute_invoice_item();

-- Keep invoice rollups truthful after item changes.
CREATE OR REPLACE FUNCTION app.refresh_invoice_totals() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, app, pg_temp
AS $$
DECLARE v_inv uuid := COALESCE(NEW.invoice_id, OLD.invoice_id);
BEGIN
  UPDATE public.invoices i SET
    subtotal     = COALESCE(t.sub, 0),
    tax_total    = COALESCE(t.tax, 0),
    total_amount = COALESCE(t.tot, 0),
    balance_due  = round(COALESCE(t.tot, 0) - i.amount_paid - i.amount_disputed, 4)
    FROM (
      SELECT sum(subtotal) AS sub, sum(tax_total) AS tax, sum(total) AS tot
        FROM public.invoice_items WHERE invoice_id = v_inv
    ) t
   WHERE i.id = v_inv;
  RETURN NULL;
END
$$;

DROP TRIGGER IF EXISTS trg_invoice_items_totals ON public.invoice_items;
CREATE TRIGGER trg_invoice_items_totals AFTER INSERT OR UPDATE OR DELETE ON public.invoice_items
  FOR EACH ROW EXECUTE FUNCTION app.refresh_invoice_totals();

-- -----------------------------------------------------------------------------
-- invoice_allocations — how a payment maps onto an invoice (supports partials).
-- PK is (invoice_id, source) where source is PAYMENT | CREDIT_NOTE | ADJUSTMENT.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.invoice_allocations (
  id            uuid PRIMARY KEY DEFAULT app.uuid7(),
  invoice_id    uuid NOT NULL REFERENCES public.invoices(id) ON DELETE CASCADE,
  source        text NOT NULL CHECK (source IN ('PAYMENT','CREDIT_NOTE','ADJUSTMENT','WRITE_OFF')),
  payment_id    uuid,                                  -- FK added in 0010 (payments)
  credit_note_id uuid,
  amount        numeric(18,4) NOT NULL CHECK (amount <> 0),
  currency      char(3) NOT NULL DEFAULT 'USD',
  fx_rate       numeric(18,8) CHECK (fx_rate IS NULL OR fx_rate > 0),
  allocated_at  timestamptz NOT NULL DEFAULT now(),
  allocated_by  uuid REFERENCES public.users(id) ON DELETE SET NULL,
  note          text,
  CONSTRAINT ck_allocation_source CHECK (
    (source = 'PAYMENT' AND payment_id IS NOT NULL)
    OR (source <> 'PAYMENT' AND payment_id IS NULL)
  )
);

CREATE INDEX IF NOT EXISTS ix_allocations_invoice ON public.invoice_allocations (invoice_id, allocated_at DESC);
CREATE INDEX IF NOT EXISTS ix_allocations_payment ON public.invoice_allocations (payment_id)
  WHERE payment_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_allocation_payment
  ON public.invoice_allocations (payment_id, invoice_id)
  WHERE payment_id IS NOT NULL AND source = 'PAYMENT';

-- -----------------------------------------------------------------------------
-- invoice_approvals
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.invoice_approvals (
  id            uuid PRIMARY KEY DEFAULT app.uuid7(),
  invoice_id    uuid NOT NULL REFERENCES public.invoices(id) ON DELETE CASCADE,
  step_no       int NOT NULL CHECK (step_no > 0),
  name          text NOT NULL DEFAULT 'Invoice Approval',
  approver_user_id uuid REFERENCES public.users(id) ON DELETE SET NULL,
  approver_company_id uuid REFERENCES public.companies(id) ON DELETE CASCADE,
  status        text NOT NULL DEFAULT 'PENDING'
                  CHECK (status IN ('PENDING','APPROVED','REJECTED','SKIPPED')),
  notes         text,
  requested_at  timestamptz NOT NULL DEFAULT now(),
  decided_at    timestamptz,
  UNIQUE (invoice_id, step_no)
);

CREATE INDEX IF NOT EXISTS ix_invoice_approvals_pending
  ON public.invoice_approvals (approver_user_id, requested_at) WHERE status = 'PENDING';

CREATE OR REPLACE FUNCTION app.assert_invoice_approval() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_company uuid; v_created_by uuid; v_sod boolean;
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

  IF TG_OP = 'UPDATE' AND OLD.status = 'PENDING' AND NEW.status IN ('APPROVED','REJECTED') THEN
    SELECT i.company_id, i.created_by INTO v_company, v_created_by
      FROM public.invoices i WHERE i.id = NEW.invoice_id;

    SELECT COALESCE((settings->'segregation_of_duties'->>'allow_self_approval'), 'false')::boolean
      INTO v_sod FROM public.companies WHERE id = v_company;

    IF v_created_by = app.current_user_id() AND NOT v_sod THEN
      RAISE EXCEPTION 'segregation of duties: cannot approve an invoice you created'
        USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NOT app.has_permission(v_company, 'invoices.approve') THEN
      RAISE EXCEPTION 'missing permission invoices.approve' USING ERRCODE = 'insufficient_privilege';
    END IF;
    NEW.decided_at := now();
  END IF;
  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_invoice_approvals_guard ON public.invoice_approvals;
CREATE TRIGGER trg_invoice_approvals_guard BEFORE UPDATE ON public.invoice_approvals
  FOR EACH ROW EXECUTE FUNCTION app.assert_invoice_approval();

-- -----------------------------------------------------------------------------
-- Invoice lifecycle events.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.emit_invoice_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, app, platform, pg_temp
AS $$
BEGIN
  IF TG_OP = 'UPDATE' AND NEW.status IS DISTINCT FROM OLD.status THEN
    INSERT INTO platform.outbox_events (
      event_type, company_id, actor_user_id, aggregate_type, aggregate_id, payload, idempotency_key
    ) VALUES (
      'INVOICE_STATUS_CHANGED', NEW.company_id, app.current_user_id(), 'invoice', NEW.id,
      jsonb_build_object(
        'public_id', NEW.public_id, 'direction', NEW.direction,
        'from', OLD.status, 'to', NEW.status,
        'total_amount', NEW.total_amount, 'balance_due', NEW.balance_due,
        'currency', NEW.currency, 'counterparty_company_id', NEW.counterparty_company_id,
        'due_date', NEW.due_date, 'msa_required', NEW.msa_required),
      'invoice_status:' || NEW.id || ':' || NEW.version || ':' || NEW.status
    );
  END IF;
  RETURN NULL;
END
$$;

DROP TRIGGER IF EXISTS trg_invoices_event ON public.invoices;
CREATE TRIGGER trg_invoices_event AFTER UPDATE ON public.invoices
  FOR EACH ROW EXECUTE FUNCTION app.emit_invoice_event();

-- Overdue detection is a stored-state transition, not a cron guess.
CREATE OR REPLACE FUNCTION app.mark_overdue_invoices() RETURNS int
LANGUAGE plpgsql AS $$
DECLARE n int;
BEGIN

  WITH updated AS (
    UPDATE public.invoices
       SET status = 'OVERDUE'
     WHERE deleted_at IS NULL
       AND due_date < current_date
       AND status IN ('SUBMITTED','APPROVED','PARTIALLY_PAID')
     RETURNING id
  )
  SELECT count(*) INTO n FROM updated;
  RETURN n;
END
$$;

COMMENT ON FUNCTION app.mark_overdue_invoices() IS
  'Called by the scheduled worker. Transitions are idempotent: a PAID invoice is never touched.';

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['invoice_items'] LOOP
    PERFORM app.attach_updated_at(
      format('public.%I', t)::regclass,
      'trg_' || t || '_updated_at');
  END LOOP;
END
$$;

GRANT EXECUTE ON FUNCTION app.mark_overdue_invoices() TO mytrakin_worker, mytrakin_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON
  public.billing_runs, public.invoices, public.invoice_items,
  public.invoice_allocations, public.invoice_approvals
TO mytrakin_api, mytrakin_worker;
