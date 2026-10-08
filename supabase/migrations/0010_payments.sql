-- =============================================================================
-- MyTrakin :: 0010_payments.sql
-- PAYMENTS module: processor accounts, bank connections (Plaid), transactions,
-- reconciliation, payments, allocations, requests and schedules.
--
-- Invariants enforced in the database:
--   P1  money never moves without an explicit authorization + processor record
--   P2  allocations cannot exceed the payment amount
--   P3  invoice balances derive from allocations, never from a client-provided total
--   P4  every incoming webhook is idempotent (processor_event_id unique)
--   P5  raw banking credentials are never stored; only opaque access tokens
-- =============================================================================

-- -----------------------------------------------------------------------------
-- payment_accounts — the company's relationship with a money-movement processor.
-- Plaid connects bank accounts; it does not move money. That is this table's job.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.payment_accounts (
  id                 uuid PRIMARY KEY DEFAULT app.uuid7(),
  company_id         uuid NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
  public_id          text NOT NULL UNIQUE,
  provider           text NOT NULL
                       CHECK (provider IN ('STRIPE','ADYEN','CHECK','ACH','WIRE','MANUAL','OTHER')),
  account_type       text NOT NULL CHECK (account_type IN ('CHECKING','SAVINGS','CREDIT','PREPAID','OTHER')),
  display_name       text NOT NULL,
  currency           char(3) NOT NULL DEFAULT 'USD',
  processor_account_ref text NOT NULL,       -- opaque reference at the processor; never a secret
  status             text NOT NULL DEFAULT 'PENDING'
                       CHECK (status IN ('PENDING','ACTIVE','RESTRICTED','CLOSED')),
  payout_enabled     boolean NOT NULL DEFAULT false,
  balance_available  numeric(18,4) NOT NULL DEFAULT 0,
  balance_pending    numeric(18,4) NOT NULL DEFAULT 0,
  last_synced_at     timestamptz,
  created_by         uuid REFERENCES public.users(id) ON DELETE SET NULL,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  deleted_at         timestamptz,
  CONSTRAINT ck_payment_account_currency CHECK (currency ~ '^[A-Z]{3}$'),
  UNIQUE (company_id, provider, processor_account_ref)
);

COMMENT ON COLUMN public.payment_accounts.balance_available IS
  'Processor-reported. Server-read-only; clients may not set it.';
COMMENT ON COLUMN public.payment_accounts.processor_account_ref IS
  'Opaque processor identifier. No API keys, tokens or banking credentials.';

CREATE INDEX IF NOT EXISTS ix_payment_accounts_company
  ON public.payment_accounts (company_id, status) WHERE deleted_at IS NULL;

CREATE OR REPLACE FUNCTION app.assign_payment_account_public_id() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_try int := 0; v_id text;
BEGIN

  LOOP
    v_id := app.gen_public_id('PA', 8);
    EXIT WHEN NOT EXISTS (SELECT 1 FROM public.payment_accounts WHERE public_id = v_id);
    v_try := v_try + 1;
    IF v_try > 5 THEN RAISE EXCEPTION 'could not allocate payment account public_id'; END IF;
  END LOOP;
  NEW.public_id := v_id;
  RETURN NEW;
END
$$;

SELECT app.attach_triggers('public.payment_accounts'::regclass);
DROP TRIGGER IF EXISTS trg_payment_accounts_public_id ON public.payment_accounts;
CREATE TRIGGER trg_payment_accounts_public_id BEFORE INSERT ON public.payment_accounts
  FOR EACH ROW WHEN (NEW.public_id IS NULL)
  EXECUTE FUNCTION app.assign_payment_account_public_id();

-- -----------------------------------------------------------------------------
-- bank_connections — Plaid items. Access tokens are ENCRYPTED before storage (P5);
-- the plaintext token never leaves the API process and is never logged.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.bank_connections (
  id                uuid PRIMARY KEY DEFAULT app.uuid7(),
  company_id        uuid NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
  public_id         text NOT NULL UNIQUE,
  provider          text NOT NULL DEFAULT 'PLAID' CHECK (provider = 'PLAID'),
  owner_user_id     uuid NOT NULL REFERENCES public.users(id) ON DELETE RESTRICT,
  institution_name  text,
  institution_id    text,
  item_id_encrypted bytea NOT NULL,          -- encrypted provider item reference
  access_token_encrypted bytea NOT NULL,     -- encrypted short-lived access token
  key_version       int NOT NULL DEFAULT 1,
  status            text NOT NULL DEFAULT 'PENDING'
                      CHECK (status IN ('PENDING','CONNECTED','VERIFIED','VERIFICATION_FAILED',
                                        'DISCONNECTED','REAUTH_REQUIRED','ERROR')),
  verification_state public.verification_state NOT NULL DEFAULT 'UNVERIFIED',
  ownership_verified boolean NOT NULL DEFAULT false,
  consent_expires_at timestamptz,
  last_synced_at    timestamptz,
  sync_cursor       text,
  transaction_count int NOT NULL DEFAULT 0,
  error_detail      jsonb,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  deleted_at        timestamptz,
  UNIQUE (company_id, provider, institution_id)
);

COMMENT ON TABLE public.bank_connections IS
  'Bank account link (Plaid). Tokens are encrypted at rest; P5 forbids storing banking credentials.';
COMMENT ON COLUMN public.bank_connections.verification_state IS
  'Owner/name verification result. Plaid signals it; the platform records the evidence.';

CREATE INDEX IF NOT EXISTS ix_bank_connections_company
  ON public.bank_connections (company_id, status) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_bank_connections_reauth
  ON public.bank_connections (company_id) WHERE status IN ('REAUTH_REQUIRED','ERROR') AND deleted_at IS NULL;

CREATE OR REPLACE FUNCTION app.assign_bank_connection_public_id() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_try int := 0; v_id text;
BEGIN

  LOOP
    v_id := app.gen_public_id('BC', 8);
    EXIT WHEN NOT EXISTS (SELECT 1 FROM public.bank_connections WHERE public_id = v_id);
    v_try := v_try + 1;
    IF v_try > 5 THEN RAISE EXCEPTION 'could not allocate bank connection public_id'; END IF;
  END LOOP;
  NEW.public_id := v_id;
  RETURN NEW;
END
$$;

SELECT app.attach_triggers('public.bank_connections'::regclass);
DROP TRIGGER IF EXISTS trg_bank_connections_public_id ON public.bank_connections;
CREATE TRIGGER trg_bank_connections_public_id BEFORE INSERT ON public.bank_connections
  FOR EACH ROW WHEN (NEW.public_id IS NULL)
  EXECUTE FUNCTION app.assign_bank_connection_public_id();

-- -----------------------------------------------------------------------------
-- bank_accounts — connected institutions, masked. account_number_masked is
-- derived from the provider token at link time; full numbers are never stored.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.bank_accounts (
  id                   uuid PRIMARY KEY DEFAULT app.uuid7(),
  bank_connection_id   uuid NOT NULL REFERENCES public.bank_connections(id) ON DELETE CASCADE,
  company_id           uuid NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
  public_id            text NOT NULL UNIQUE,
  institution_name     text NOT NULL,
  name                 text,
  account_number_masked text NOT NULL,       -- e.g. '****4321'
  account_type         text NOT NULL
                         CHECK (account_type IN ('CHECKING','SAVINGS','MONEY_MARKET','CREDIT','LOAN','OTHER')),
  subtype              text,
  currency             char(3) NOT NULL DEFAULT 'USD',
  provider_account_id_encrypted bytea,      -- encrypted provider account reference
  status               text NOT NULL DEFAULT 'PENDING'
                         CHECK (status IN ('PENDING','CONNECTED','VERIFIED','VERIFICATION_FAILED',
                                           'DISCONNECTED','REAUTH_REQUIRED')),
  verification_state   public.verification_state NOT NULL DEFAULT 'UNVERIFIED',
  is_primary           boolean NOT NULL DEFAULT false,
  available_balance    numeric(18,4),
  current_balance      numeric(18,4),
  connected_at         timestamptz NOT NULL DEFAULT now(),
  verified_at          timestamptz,
  last_synced_at       timestamptz,
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now(),
  deleted_at           timestamptz
);

COMMENT ON COLUMN public.bank_accounts.account_number_masked IS
  'Masked display value only. Full account numbers are never persisted.';
COMMENT ON COLUMN public.bank_accounts.is_primary IS
  'Exactly one primary per company; enforced by app.set_primary_bank_account().';

CREATE INDEX IF NOT EXISTS ix_bank_accounts_company
  ON public.bank_accounts (company_id, status) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_bank_accounts_primary
  ON public.bank_accounts (company_id) WHERE is_primary AND deleted_at IS NULL;

CREATE OR REPLACE FUNCTION app.assign_bank_account_public_id() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_try int := 0; v_id text;
BEGIN

  LOOP
    v_id := app.gen_public_id('BA', 8);
    EXIT WHEN NOT EXISTS (SELECT 1 FROM public.bank_accounts WHERE public_id = v_id);
    v_try := v_try + 1;
    IF v_try > 5 THEN RAISE EXCEPTION 'could not allocate bank account public_id'; END IF;
  END LOOP;
  NEW.public_id := v_id;
  RETURN NEW;
END
$$;

SELECT app.attach_triggers('public.bank_accounts'::regclass);
DROP TRIGGER IF EXISTS trg_bank_accounts_public_id ON public.bank_accounts;
CREATE TRIGGER trg_bank_accounts_public_id BEFORE INSERT ON public.bank_accounts
  FOR EACH ROW WHEN (NEW.public_id IS NULL)
  EXECUTE FUNCTION app.assign_bank_account_public_id();

CREATE OR REPLACE FUNCTION app.set_primary_bank_account() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  -- Migrations, seeds and background workers have no end-user identity.
  -- mytrakin_api is neither superuser nor BYPASSRLS, so request traffic
  -- always falls through to the real checks below.
  IF app.is_trusted_context() THEN
    RETURN NEW;
  END IF;

  IF NEW.is_primary AND NEW.deleted_at IS NULL THEN
    UPDATE public.bank_accounts SET is_primary = false
     WHERE company_id = NEW.company_id AND id <> NEW.id AND is_primary;
  END IF;
  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_bank_accounts_primary ON public.bank_accounts;
CREATE TRIGGER trg_bank_accounts_primary BEFORE INSERT OR UPDATE OF is_primary ON public.bank_accounts
  FOR EACH ROW WHEN (NEW.is_primary) EXECUTE FUNCTION app.set_primary_bank_account();

-- -----------------------------------------------------------------------------
-- bank_transactions — normalised, append-only ledger of account activity.
-- provider_transaction_id + bank_account_id is unique: re-sync is a no-op (P4).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.bank_transactions (
  id                    uuid PRIMARY KEY DEFAULT app.uuid7(),
  bank_account_id       uuid NOT NULL REFERENCES public.bank_accounts(id) ON DELETE CASCADE,
  company_id            uuid NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
  provider_transaction_id text NOT NULL,
  posted_at             timestamptz NOT NULL,
  authorized_at         timestamptz,
  amount                numeric(18,4) NOT NULL,     -- signed: negative = money out
  currency              char(3) NOT NULL DEFAULT 'USD',
  direction             text GENERATED ALWAYS AS (CASE WHEN amount < 0 THEN 'CREDIT' ELSE 'DEBIT' END) STORED,
  description_raw       text,
  merchant_name         text,
  normalized_description text,
  category              text,
  check_number          text,
  payment_channel       text,
  is_pending            boolean NOT NULL DEFAULT false,
  is_reconciled         boolean NOT NULL DEFAULT false,
  match_status          text NOT NULL DEFAULT 'UNMATCHED'
                          CHECK (match_status IN ('UNMATCHED','SUGGESTED','MATCHED','PARTIALLY_MATCHED','IGNORED')),
  match_confidence      numeric(5,4) CHECK (match_confidence IS NULL
                                           OR (match_confidence >= 0 AND match_confidence <= 1)),
  duplicate_of_id       uuid REFERENCES public.bank_transactions(id) ON DELETE SET NULL,
  raw_payload_hash      char(64),
  imported_at           timestamptz NOT NULL DEFAULT now(),
  created_at            timestamptz NOT NULL DEFAULT now(),
  UNIQUE (bank_account_id, provider_transaction_id),
  CONSTRAINT ck_txn_currency CHECK (currency ~ '^[A-Z]{3}$')
);

COMMENT ON COLUMN public.bank_transactions.amount IS
  'Append-only transaction ledger. UNIQUE(bank_account_id, provider_transaction_id) makes re-sync idempotent.';
COMMENT ON COLUMN public.bank_transactions.amount IS
  'Signed amount as provided by the institution. Debits are negative.';

CREATE INDEX IF NOT EXISTS ix_txn_company_time
  ON public.bank_transactions (company_id, posted_at DESC);
CREATE INDEX IF NOT EXISTS ix_txn_unmatched
  ON public.bank_transactions (company_id, posted_at DESC)
  WHERE match_status IN ('UNMATCHED','SUGGESTED');
CREATE INDEX IF NOT EXISTS ix_txn_amount_lookup
  ON public.bank_transactions (company_id, amount, posted_at);
CREATE INDEX IF NOT EXISTS ix_txn_hash ON public.bank_transactions (raw_payload_hash)
  WHERE raw_payload_hash IS NOT NULL;

-- Match state may be re-evaluated by the reconciliation engine without touching
-- ledger facts. Trigger name ordering is deliberate: Postgres fires BEFORE
-- triggers in name order, so the ledger-fact guard runs first and a tampered
-- amount is rejected even when the caller also changes the match status.
--
-- There is no trusted-context escape. A bank ledger an operator can rewrite is
-- not evidence of anything; corrections are compensating entries, never edits.
CREATE OR REPLACE FUNCTION app.assert_txn_ledger_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'bank_transactions is append-only; DELETE rejected'
      USING ERRCODE = 'restrict_violation';
  END IF;

  IF NEW.amount IS DISTINCT FROM OLD.amount
     OR NEW.posted_at IS DISTINCT FROM OLD.posted_at
     OR NEW.provider_transaction_id IS DISTINCT FROM OLD.provider_transaction_id
     OR NEW.bank_account_id IS DISTINCT FROM OLD.bank_account_id
     OR NEW.currency IS DISTINCT FROM OLD.currency
     OR NEW.description_raw IS DISTINCT FROM OLD.description_raw THEN
    RAISE EXCEPTION
      'bank transaction ledger fields are immutable (provider id %)',
      OLD.provider_transaction_id
      USING ERRCODE = 'restrict_violation',
            HINT = 'Post a correcting entry instead of editing a bank transaction.';
  END IF;

  RETURN NEW;
END
$$;

COMMENT ON FUNCTION app.assert_txn_ledger_immutable() IS
  'Rejects DELETE and any change to a ledger fact. Match and duplicate bookkeeping remain writable.';

DROP TRIGGER IF EXISTS trg_txn_immutable ON public.bank_transactions;
DROP TRIGGER IF EXISTS trg_txn_match_guard ON public.bank_transactions;
DROP TRIGGER IF EXISTS trg_txn_10_ledger_facts ON public.bank_transactions;
CREATE TRIGGER trg_txn_10_ledger_facts
  BEFORE UPDATE OR DELETE ON public.bank_transactions
  FOR EACH ROW EXECUTE FUNCTION app.assert_txn_ledger_immutable();

-- -----------------------------------------------------------------------------
-- payments — money movement. Always has a processor reference.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.payments (
  id                uuid PRIMARY KEY DEFAULT app.uuid7(),
  public_id         text NOT NULL UNIQUE,
  company_id        uuid NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
  direction         public.invoice_direction NOT NULL,       -- RECEIVABLE = inbound
  status            public.payment_status NOT NULL DEFAULT 'SCHEDULED',
  payment_account_id uuid REFERENCES public.payment_accounts(id) ON DELETE RESTRICT,
  bank_account_id   uuid REFERENCES public.bank_accounts(id) ON DELETE SET NULL,
  counterparty_company_id uuid REFERENCES public.companies(id) ON DELETE RESTRICT,
  counterparty_name text,
  amount            numeric(18,4) NOT NULL CHECK (amount > 0),
  currency          char(3) NOT NULL DEFAULT 'USD',
  fee_amount        numeric(18,4) NOT NULL DEFAULT 0 CHECK (fee_amount >= 0),
  net_amount        numeric(18,4),
  fx_rate           numeric(18,8) CHECK (fx_rate IS NULL OR fx_rate > 0),
  original_amount   numeric(18,4),
  original_currency char(3),
  payment_method    text NOT NULL DEFAULT 'ACH'
                      CHECK (payment_method IN ('ACH','WIRE','CARD','CHECK','CASH','CRYPTO','OTHER')),
  scheduled_for     date,
  initiated_at      timestamptz,
  completed_at      timestamptz,
  failed_at         timestamptz,
  failure_reason    text,
  -- P1: authorization trail. Money cannot move without one of these.
  authorization_type text NOT NULL DEFAULT 'EXPLICIT'
                       CHECK (authorization_type IN ('EXPLICIT','POLICY','SCHEDULED','AUTOMATION')),
  authorized_by     uuid REFERENCES public.users(id) ON DELETE SET NULL,
  authorized_at     timestamptz,
  authorization_ref text,
  processor         text NOT NULL DEFAULT 'MANUAL'
                      CHECK (processor IN ('STRIPE','ADYEN','CHECK','ACH','WIRE','MANUAL','OTHER')),
  processor_payment_ref text,             -- set once the processor accepts
  idempotency_key   text UNIQUE,
  reconciliation_status text NOT NULL DEFAULT 'PENDING'
                       CHECK (reconciliation_status IN ('PENDING','RECONCILED','UNMATCHED','MANUAL')),
  bank_transaction_id uuid REFERENCES public.bank_transactions(id) ON DELETE SET NULL,
  metadata          jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_by        uuid REFERENCES public.users(id) ON DELETE SET NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  deleted_at        timestamptz,
  CONSTRAINT ck_payment_currency CHECK (currency ~ '^[A-Z]{3}$'),
  CONSTRAINT ck_payment_ref CHECK (
    status IN ('SCHEDULED','INITIATED','PROCESSING')
    OR processor_payment_ref IS NOT NULL
    OR bank_transaction_id IS NOT NULL
  )
);

COMMENT ON COLUMN public.payments.amount IS
  'Money movement record. A payment cannot reach COMPLETED without a processor reference or a bank transaction (P1).';
COMMENT ON COLUMN public.payments.authorization_ref IS
  'Opaque reference to the authorization decision (explicit confirmation, policy rule, or approved automation).';

CREATE INDEX IF NOT EXISTS ix_payments_company
  ON public.payments (company_id, created_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_payments_pending
  ON public.payments (company_id, scheduled_for)
  WHERE status IN ('SCHEDULED','INITIATED','PROCESSING') AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_payments_completed
  ON public.payments (company_id, completed_at DESC)
  WHERE status = 'COMPLETED' AND deleted_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_payments_processor_ref
  ON public.payments (processor, processor_payment_ref)
  WHERE processor_payment_ref IS NOT NULL;

CREATE OR REPLACE FUNCTION app.assign_payment_public_id() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_try int := 0; v_id text;
BEGIN

  LOOP
    v_id := app.gen_public_id('PM', 8);
    EXIT WHEN NOT EXISTS (SELECT 1 FROM public.payments WHERE public_id = v_id);
    v_try := v_try + 1;
    IF v_try > 5 THEN RAISE EXCEPTION 'could not allocate payment public_id'; END IF;
  END LOOP;
  NEW.public_id := v_id;
  RETURN NEW;
END
$$;

SELECT app.attach_triggers('public.payments'::regclass);
DROP TRIGGER IF EXISTS trg_payments_public_id ON public.payments;
CREATE TRIGGER trg_payments_public_id BEFORE INSERT ON public.payments
  FOR EACH ROW WHEN (NEW.public_id IS NULL)
  EXECUTE FUNCTION app.assign_payment_public_id();

-- Payment lifecycle + segregation of duties.
CREATE OR REPLACE FUNCTION app.assert_payment_transition() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  v_from public.payment_status := OLD.status;
  v_to   public.payment_status := NEW.status;
  v_allowed text[];
  v_sod boolean;
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
    WHEN 'SCHEDULED'  THEN ARRAY['INITIATED','CANCELLED','FAILED']::text[]
    WHEN 'INITIATED'  THEN ARRAY['PROCESSING','COMPLETED','FAILED','CANCELLED']::text[]
    WHEN 'PROCESSING' THEN ARRAY['COMPLETED','FAILED']::text[]
    WHEN 'COMPLETED'  THEN ARRAY['PARTIALLY_REFUNDED','REFUNDED']::text[]
    WHEN 'PARTIALLY_REFUNDED' THEN ARRAY['REFUNDED']::text[]
    ELSE ARRAY[]::text[]
  END;

  IF NOT (v_to::text = ANY (v_allowed)) THEN
    RAISE EXCEPTION 'invalid payment transition % -> %', v_from, v_to
      USING ERRCODE = 'check_violation';
  END IF;

  -- P1: initiation requires payments.initiate plus recorded authorization.
  IF v_to IN ('INITIATED','PROCESSING') THEN
    IF NOT app.has_permission(NEW.company_id, 'payments.initiate') THEN
      RAISE EXCEPTION 'missing permission payments.initiate' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.authorized_at IS NULL THEN
      RAISE EXCEPTION 'payment % has no recorded authorization', NEW.public_id
        USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.authorization_type = 'EXPLICIT'
       AND NEW.authorized_by IS NOT NULL
       AND NEW.authorized_by = NEW.created_by THEN
      -- allowed only if company policy permits; checked below
      NULL;
    END IF;
    NEW.initiated_at := COALESCE(NEW.initiated_at, now());
  END IF;

  IF v_to = 'COMPLETED' THEN
    IF NOT app.has_permission(NEW.company_id, 'payments.complete') THEN
      RAISE EXCEPTION 'missing permission payments.complete' USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- segregation of duties for the person who created it
    SELECT COALESCE((settings->'segregation_of_duties'->>'require_distinct_initiator_and_approver'),
                    'true')::boolean INTO v_sod FROM public.companies WHERE id = NEW.company_id;
    IF v_sod AND NEW.created_by = app.current_user_id() AND NEW.processor <> 'CHECK'
       AND NEW.bank_transaction_id IS NULL THEN
      RAISE EXCEPTION 'segregation of duties: initiator cannot complete this payment'
        USING ERRCODE = 'insufficient_privilege';
    END IF;
    NEW.completed_at := COALESCE(NEW.completed_at, now());
    NEW.net_amount := round(NEW.amount - NEW.fee_amount, 4);
  END IF;

  IF v_to = 'FAILED' THEN
    NEW.failed_at := now();
  END IF;

  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_payments_transition ON public.payments;
CREATE TRIGGER trg_payments_transition BEFORE UPDATE ON public.payments
  FOR EACH ROW EXECUTE FUNCTION app.assert_payment_transition();

CREATE OR REPLACE FUNCTION app.assert_payment_insert() RETURNS trigger
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
  IF NEW.amount <= 0 THEN
    RAISE EXCEPTION 'payment amount must be positive' USING ERRCODE = 'check_violation';
  END IF;
  IF NOT app.has_permission(NEW.company_id, 'payments.create') THEN
    RAISE EXCEPTION 'missing permission payments.create' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_payments_guard ON public.payments;
CREATE TRIGGER trg_payments_guard BEFORE INSERT ON public.payments
  FOR EACH ROW EXECUTE FUNCTION app.assert_payment_insert();

-- Now that payments exists, close the FK from 0009.
ALTER TABLE public.invoice_allocations DROP CONSTRAINT IF EXISTS invoice_allocations_payment_id_fkey;
ALTER TABLE public.invoice_allocations
  ADD CONSTRAINT invoice_allocations_payment_id_fkey
  FOREIGN KEY (payment_id) REFERENCES public.payments(id) ON DELETE RESTRICT;

-- -----------------------------------------------------------------------------
-- payment_allocations — movement of value between payments and invoices (P2).
-- Total allocated can never exceed the payment amount.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.payment_allocations (
  id            uuid PRIMARY KEY DEFAULT app.uuid7(),
  payment_id    uuid NOT NULL REFERENCES public.payments(id) ON DELETE CASCADE,
  invoice_id    uuid REFERENCES public.invoices(id) ON DELETE RESTRICT,
  bank_transaction_id uuid REFERENCES public.bank_transactions(id) ON DELETE SET NULL,
  amount        numeric(18,4) NOT NULL CHECK (amount > 0),
  currency      char(3) NOT NULL DEFAULT 'USD',
  fx_rate       numeric(18,8) CHECK (fx_rate IS NULL OR fx_rate > 0),
  allocation_type text NOT NULL DEFAULT 'INVOICE'
                      CHECK (allocation_type IN ('INVOICE','UNAPPLIED','FEE','REFUND','ON_ACCOUNT')),
  confidence    numeric(5,4) CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
  matched_by    text NOT NULL DEFAULT 'MANUAL'
                  CHECK (matched_by IN ('MANUAL','AI_SUGGESTED','AUTO_ACCEPTED','SYSTEM')),
  confirmed_by  uuid REFERENCES public.users(id) ON DELETE SET NULL,
  confirmed_at  timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (payment_id, invoice_id)
);

COMMENT ON TABLE public.payment_allocations IS
  'Links money movement to invoices. matched_by distinguishes manual, AI-suggested and auto-accepted matches.';

CREATE INDEX IF NOT EXISTS ix_payalloc_payment ON public.payment_allocations (payment_id);
CREATE INDEX IF NOT EXISTS ix_payalloc_invoice ON public.payment_allocations (invoice_id);
CREATE INDEX IF NOT EXISTS ix_payalloc_txn     ON public.payment_allocations (bank_transaction_id)
  WHERE bank_transaction_id IS NOT NULL;

-- P2: allocations cannot exceed the payment.
CREATE OR REPLACE FUNCTION app.assert_allocation_within_payment() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_total numeric; v_amount numeric; v_currency char(3);
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

  SELECT amount, currency INTO v_amount, v_currency FROM public.payments WHERE id = NEW.payment_id;

  SELECT COALESCE(sum(amount), 0) INTO v_total
    FROM public.payment_allocations WHERE payment_id = NEW.payment_id;

  IF v_total + NEW.amount > v_amount THEN
    RAISE EXCEPTION 'allocations for payment % (% %) exceed the payment amount (% %)',
      NEW.payment_id, v_total, v_currency, NEW.amount, v_amount
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_payalloc_guard ON public.payment_allocations;
CREATE TRIGGER trg_payalloc_guard BEFORE INSERT OR UPDATE ON public.payment_allocations
  FOR EACH ROW EXECUTE FUNCTION app.assert_allocation_within_payment();

-- Mirror the allocation into invoice_allocations so invoice balances stay correct.
CREATE OR REPLACE FUNCTION app.sync_invoice_allocation() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, app, pg_temp
AS $$
BEGIN

  IF NEW.invoice_id IS NULL THEN RETURN NULL; END IF;

  INSERT INTO public.invoice_allocations (invoice_id, source, payment_id, amount, currency, fx_rate)
  VALUES (NEW.invoice_id, 'PAYMENT', NEW.payment_id, NEW.amount, NEW.currency, NEW.fx_rate)
  ON CONFLICT (payment_id, invoice_id) WHERE payment_id IS NOT NULL AND source = 'PAYMENT'
    DO UPDATE SET amount = EXCLUDED.amount, allocated_at = now();

  -- P3: invoice balance is recomputed from allocations only.
  UPDATE public.invoices i SET
    amount_paid = COALESCE(p.paid, 0),
    balance_due = round(i.total_amount - COALESCE(p.paid, 0) - i.amount_disputed, 4),
    status = CASE
      WHEN i.status IN ('CANCELLED','REJECTED','DRAFT','PENDING') THEN i.status
      WHEN COALESCE(p.paid, 0) >= i.total_amount THEN 'PAID'::public.invoice_status
      WHEN COALESCE(p.paid, 0) > 0 THEN 'PARTIALLY_PAID'::public.invoice_status
      ELSE i.status END
    FROM (
      SELECT COALESCE(sum(a.amount), 0) AS paid
        FROM public.invoice_allocations a
        JOIN public.payments pm ON pm.id = a.payment_id
       WHERE a.invoice_id = NEW.invoice_id AND pm.status IN ('COMPLETED','PARTIALLY_REFUNDED')
    ) p
   WHERE i.id = NEW.invoice_id;

  RETURN NULL;
END
$$;

DROP TRIGGER IF EXISTS trg_payalloc_sync ON public.payment_allocations;
CREATE TRIGGER trg_payalloc_sync AFTER INSERT OR UPDATE ON public.payment_allocations
  FOR EACH ROW EXECUTE FUNCTION app.sync_invoice_allocation();

-- -----------------------------------------------------------------------------
-- payment_matches — reconciliation suggestions and human decisions.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.payment_matches (
  id            uuid PRIMARY KEY DEFAULT app.uuid7(),
  company_id    uuid NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
  bank_transaction_id uuid NOT NULL REFERENCES public.bank_transactions(id) ON DELETE CASCADE,
  invoice_id    uuid REFERENCES public.invoices(id) ON DELETE CASCADE,
  payment_id    uuid REFERENCES public.payments(id) ON DELETE CASCADE,
  confidence    numeric(5,4) NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
  score_breakdown jsonb NOT NULL DEFAULT '{}'::jsonb,   -- amount/date/reference/counterparty
  suggestion    text NOT NULL CHECK (suggestion IN ('MATCH','REVIEW','IGNORE')),
  status        text NOT NULL DEFAULT 'SUGGESTED'
                  CHECK (status IN ('SUGGESTED','ACCEPTED','REJECTED','EXPIRED')),
  decided_by    uuid REFERENCES public.users(id) ON DELETE SET NULL,
  decided_at    timestamptz,
  decision_notes text,
  engine_version text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (bank_transaction_id, invoice_id)
);

COMMENT ON TABLE public.payment_matches IS
  'Reconciliation suggestions. AI proposes; a human confirms unless company policy auto-accepts high confidence.';

CREATE INDEX IF NOT EXISTS ix_matches_company ON public.payment_matches (company_id, status, confidence DESC);
CREATE INDEX IF NOT EXISTS ix_matches_pending ON public.payment_matches (company_id, created_at DESC)
  WHERE status = 'SUGGESTED';
CREATE INDEX IF NOT EXISTS ix_matches_txn ON public.payment_matches (bank_transaction_id);

-- -----------------------------------------------------------------------------
-- payment_requests — a company asking to be paid (payout requests, invoices due).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.payment_requests (
  id            uuid PRIMARY KEY DEFAULT app.uuid7(),
  public_id     text NOT NULL UNIQUE,
  company_id    uuid NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
  requester_company_id uuid REFERENCES public.companies(id) ON DELETE CASCADE,
  invoice_id    uuid REFERENCES public.invoices(id) ON DELETE CASCADE,
  counterparty_company_id uuid REFERENCES public.companies(id) ON DELETE RESTRICT,
  amount        numeric(18,4) NOT NULL CHECK (amount > 0),
  currency      char(3) NOT NULL DEFAULT 'USD',
  requested_by  uuid NOT NULL REFERENCES public.users(id) ON DELETE RESTRICT,
  status        text NOT NULL DEFAULT 'PENDING'
                  CHECK (status IN ('PENDING','APPROVED','SCHEDULED','FULFILLED','DECLINED','CANCELLED','EXPIRED')),
  requested_due_date date,
  approved_by   uuid REFERENCES public.users(id) ON DELETE SET NULL,
  approved_at   timestamptz,
  payment_id    uuid REFERENCES public.payments(id) ON DELETE SET NULL,
  notes         text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_payment_request_currency CHECK (currency ~ '^[A-Z]{3}$')
);

CREATE INDEX IF NOT EXISTS ix_payment_requests_company
  ON public.payment_requests (company_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_payment_requests_incoming
  ON public.payment_requests (counterparty_company_id, status, created_at DESC)
  WHERE counterparty_company_id IS NOT NULL;

CREATE OR REPLACE FUNCTION app.assign_payment_request_public_id() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_try int := 0; v_id text;
BEGIN

  LOOP
    v_id := app.gen_public_id('PR', 8);
    EXIT WHEN NOT EXISTS (SELECT 1 FROM public.payment_requests WHERE public_id = v_id);
    v_try := v_try + 1;
    IF v_try > 5 THEN RAISE EXCEPTION 'could not allocate payment request public_id'; END IF;
  END LOOP;
  NEW.public_id := v_id;
  RETURN NEW;
END
$$;

SELECT app.attach_triggers('public.payment_requests'::regclass);
DROP TRIGGER IF EXISTS trg_payment_requests_public_id ON public.payment_requests;
CREATE TRIGGER trg_payment_requests_public_id BEFORE INSERT ON public.payment_requests
  FOR EACH ROW WHEN (NEW.public_id IS NULL)
  EXECUTE FUNCTION app.assign_payment_request_public_id();

-- -----------------------------------------------------------------------------
-- payment_schedules — recurring/scheduled payments. Creating a schedule never
-- moves money; each occurrence becomes a SCHEDULED payment requiring authorization.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.payment_schedules (
  id             uuid PRIMARY KEY DEFAULT app.uuid7(),
  public_id      text NOT NULL UNIQUE,
  company_id     uuid NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
  contract_id    uuid REFERENCES public.contracts(id) ON DELETE CASCADE,
  invoice_id     uuid REFERENCES public.invoices(id) ON DELETE CASCADE,
  payment_account_id uuid REFERENCES public.payment_accounts(id) ON DELETE RESTRICT,
  frequency      public.billing_frequency NOT NULL,
  amount         numeric(18,4) CHECK (amount IS NULL OR amount > 0),
  currency       char(3) NOT NULL DEFAULT 'USD',
  payment_method text NOT NULL DEFAULT 'ACH',
  next_run_date  date NOT NULL,
  end_date       date,
  occurrences    int CHECK (occurrences IS NULL OR occurrences > 0),
  run_count      int NOT NULL DEFAULT 0,
  auto_submit    boolean NOT NULL DEFAULT false,   -- still requires authorization_type POLICY
  status         text NOT NULL DEFAULT 'ACTIVE'
                   CHECK (status IN ('ACTIVE','PAUSED','COMPLETED','CANCELLED')),
  created_by     uuid REFERENCES public.users(id) ON DELETE SET NULL,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  deleted_at     timestamptz,
  CONSTRAINT ck_schedule_dates CHECK (end_date IS NULL OR end_date >= next_run_date)
);

COMMENT ON COLUMN public.payment_schedules.next_run_date IS
  'Recurring intent. Execution still requires a per-occurrence authorization record on the resulting payment.';

CREATE INDEX IF NOT EXISTS ix_payment_schedules_due
  ON public.payment_schedules (next_run_date) WHERE status = 'ACTIVE' AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_payment_schedules_company
  ON public.payment_schedules (company_id, status);

-- Next run date advanced by frequency; handles month-end safely.
CREATE OR REPLACE FUNCTION app.advance_schedule(p_schedule_id uuid, p_from date DEFAULT current_date)
RETURNS date
LANGUAGE plpgsql AS $$
DECLARE v_freq public.billing_frequency; v_next date; v_end date;
BEGIN

  SELECT frequency, next_run_date, end_date INTO v_freq, v_next, v_end
    FROM public.payment_schedules WHERE id = p_schedule_id;

  v_next := CASE v_freq
    WHEN 'WEEKLY'   THEN p_from + 7
    WHEN 'BIWEEKLY' THEN p_from + 14
    WHEN 'MONTHLY'  THEN (p_from + interval '1 month')::date
    WHEN 'QUARTERLY'THEN (p_from + interval '3 months')::date
    ELSE p_from + 30
  END;

  UPDATE public.payment_schedules
     SET next_run_date = v_next, run_count = run_count + 1
   WHERE id = p_schedule_id;

  RETURN v_next;
END
$$;

-- -----------------------------------------------------------------------------
-- processor_webhook_events — P4: every webhook recorded once, before processing.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS platform.processor_webhook_events (
  id                uuid PRIMARY KEY DEFAULT app.uuid7(),
  provider          text NOT NULL,
  provider_event_id text NOT NULL,
  event_type        text NOT NULL,
  company_id        uuid,
  signature_verified boolean NOT NULL DEFAULT false,
  payload           jsonb NOT NULL DEFAULT '{}'::jsonb,
  payload_hash      char(64) NOT NULL,
  received_at       timestamptz NOT NULL DEFAULT now(),
  processed_at      timestamptz,
  processing_result text,
  error_message     text,
  UNIQUE (provider, provider_event_id)
);

COMMENT ON TABLE platform.processor_webhook_events IS
  'Webhook idempotency ledger. A replayed event id is acknowledged without reprocessing.';

CREATE INDEX IF NOT EXISTS ix_webhook_unprocessed
  ON platform.processor_webhook_events (received_at) WHERE processed_at IS NULL;

CREATE OR REPLACE FUNCTION platform.claim_webhook_event(
  p_provider text, p_provider_event_id text, p_event_type text,
  p_payload jsonb, p_payload_hash text
) RETURNS TABLE (should_process boolean, event_id uuid)
LANGUAGE plpgsql AS $$
DECLARE v_id uuid; v_existing uuid;
BEGIN

  SELECT id INTO v_existing FROM platform.processor_webhook_events
   WHERE provider = p_provider AND provider_event_id = p_provider_event_id;

  IF v_existing IS NOT NULL THEN
    RETURN QUERY SELECT false, v_existing;
    RETURN;
  END IF;

  INSERT INTO platform.processor_webhook_events
    (provider, provider_event_id, event_type, payload, payload_hash)
  VALUES (p_provider, p_provider_event_id, p_event_type, p_payload, p_payload_hash)
  RETURNING id INTO v_id;

  RETURN QUERY SELECT true, v_id;
END
$$;

GRANT SELECT, INSERT, UPDATE, DELETE ON
  public.payment_accounts, public.bank_connections, public.bank_accounts,
  public.bank_transactions, public.payments, public.payment_allocations,
  public.payment_matches, public.payment_requests, public.payment_schedules
TO mytrakin_api, mytrakin_worker;
GRANT EXECUTE ON FUNCTION platform.claim_webhook_event(text, text, text, jsonb, text)
TO mytrakin_api, mytrakin_worker;
GRANT EXECUTE ON FUNCTION app.advance_schedule(uuid, date) TO mytrakin_worker, mytrakin_api;

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['payment_matches','payment_requests','payment_schedules'] LOOP
    PERFORM app.attach_updated_at(
      format('public.%I', t)::regclass,
      'trg_' || t || '_updated_at');
  END LOOP;
END
$$;
