-- =============================================================================
-- MyTrakin :: 0001_foundation.sql
-- Extensions, schemas, roles, shared helpers, core infrastructure tables.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Extensions
-- -----------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS "pgcrypto";     -- gen_random_uuid, digest, crypt
CREATE EXTENSION IF NOT EXISTS "vector";       -- pgvector embeddings (RAG)
CREATE EXTENSION IF NOT EXISTS "pg_trgm";      -- fuzzy search on names/titles
CREATE EXTENSION IF NOT EXISTS "btree_gist";   -- exclusion constraints (overlap)
CREATE EXTENSION IF NOT EXISTS "unaccent";     -- diacritic-insensitive search
CREATE EXTENSION IF NOT EXISTS "citext";       -- case-insensitive email/identifier columns

-- -----------------------------------------------------------------------------
-- Schemas
--   app         shared helpers + session identity (readable by all app roles)
--   platform    cross-cutting infra: audit, outbox, idempotency, flags
-- -----------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS app;
CREATE SCHEMA IF NOT EXISTS platform;

COMMENT ON SCHEMA app IS 'Shared helper functions and request-scoped session identity.';
COMMENT ON SCHEMA platform IS 'Cross-cutting infrastructure (audit, events, idempotency, flags).';

-- -----------------------------------------------------------------------------
-- Roles
--   mytrakin_migrator  owns the schema; only CI may connect as this role
--   mytrakin_api       the request-serving role; subject to RLS, no DDL
--   mytrakin_worker    background workers; may bypass RLS for queue-owned rows
--   mytrakin_readonly  analytics / support read-only replica access
--
-- Neither app role may bypass RLS except mytrakin_worker, whose bypass is
-- constrained by the fact that it never receives end-user credentials.
-- -----------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mytrakin_api') THEN
    CREATE ROLE mytrakin_api NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mytrakin_worker') THEN
    CREATE ROLE mytrakin_worker NOLOGIN BYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mytrakin_readonly') THEN
    CREATE ROLE mytrakin_readonly NOLOGIN;
  END IF;
END
$$;

GRANT USAGE ON SCHEMA public, app, platform TO mytrakin_api, mytrakin_worker, mytrakin_readonly;

-- -----------------------------------------------------------------------------
-- Global search path hardening: never let unqualified CREATE shadow app tables
-- -----------------------------------------------------------------------------
ALTER ROLE mytrakin_api SET search_path = public, app, platform;
ALTER ROLE mytrakin_worker SET search_path = public, app, platform;
ALTER ROLE mytrakin_readonly SET search_path = public, app, platform;

-- =============================================================================
-- app schema: identity + shared helpers
-- =============================================================================

-- Randomness helper.
-- Supabase installs pgcrypto into its own `extensions` schema, so gen_random_bytes
-- is not on the default search_path of SECURITY DEFINER functions. This resolves
-- it once and falls back to gen_random_uuid() (core pg_catalog since PG13) so the
-- migration works on vanilla PostgreSQL, Supabase and RLS-forced deployments.
CREATE OR REPLACE FUNCTION app.random_hex(p_bytes int)
RETURNS text
LANGUAGE plpgsql STABLE
AS $$
DECLARE
  v_hex text;
BEGIN

  IF to_regprocedure('extensions.gen_random_bytes(integer)') IS NOT NULL THEN
    EXECUTE format('SELECT encode(extensions.gen_random_bytes(%s), ''hex'')', p_bytes)
      INTO v_hex;
    RETURN v_hex;
  ELSIF to_regprocedure('public.gen_random_bytes(integer)') IS NOT NULL THEN
    EXECUTE format('SELECT encode(public.gen_random_bytes(%s), ''hex'')', p_bytes)
      INTO v_hex;
    RETURN v_hex;
  END IF;

  -- gen_random_uuid() is always available; hex-chop it into the requested length.
  v_hex := replace(gen_random_uuid()::text, '-', '')
        || replace(gen_random_uuid()::text, '-', '');
  RETURN left(v_hex, p_bytes * 2);
END
$$;

COMMENT ON FUNCTION app.random_hex(int) IS
  'Hex-encoded random bytes. Schema-agnostic wrapper around pgcrypto so SECURITY DEFINER functions work under any search_path.';

-- -----------------------------------------------------------------------------
-- UUIDv7 (time-ordered, non-enumerable, index friendly)
-- 48 bits of millisecond timestamp give natural index locality, which matters
-- once the tables hold millions of rows.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.uuid7() RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE
  v_now_ms bigint := (EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::bigint;
  v_rnd    text;
BEGIN

  -- 16 bytes / 32 hex digits:
  --   hex 0..11   48-bit big-endian millisecond timestamp   (12 chars)
  --   hex 12..15  version nibble '7' + 12 random bits       ( 4 chars)
  --   hex 16      variant nibble (8..b) + 3 random bits     ( 1 char)
  --   hex 17..31  56 random bits                            (15 chars)
  v_rnd := app.random_hex(10);

  RETURN (
      lpad(to_hex(v_now_ms), 12, '0')            -- 12
   || '7' || substr(v_rnd, 1, 3)               --  4
   || substr('89ab', 1 + (('x' || substr(v_rnd, 4, 1))::bit(4)::int % 4), 1)
                                                   --  1
   || substr(v_rnd, 5, 15)                      -- 15
  )::uuid;                                      -- = 32 hex digits
END
$$;

-- -----------------------------------------------------------------------------
-- Human-readable public IDs
--   Format: <prefix><8 chars Crockford base32>. Uniqueness enforced by the
--   UNIQUE constraint on each table's public_id column; collisions are retried
--   by the generating trigger.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.gen_public_id(p_prefix text, p_len int DEFAULT 8)
RETURNS text
LANGUAGE sql
AS $$
  SELECT upper(p_prefix)
      || substr(
           translate(
             encode(gen_random_bytes(16), 'hex'),
             '0123456789abcdefghijklmnopqrstuv',
             '0123456789ABCDEFGHJKMNPQRSTVWXYZ'
           ),
           1, p_len
         );
$$;

COMMENT ON FUNCTION app.gen_public_id(text, int) IS
  'Opaque public identifier. Crockford base32 (no I, L, O, U) to avoid transcription errors.';

-- -----------------------------------------------------------------------------
-- Request-scoped session identity.
-- The API sets these with SET LOCAL inside every transaction; RLS policies read
-- them. Defaults are "no identity" so a missing SET fails closed.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.current_user_id() RETURNS uuid
LANGUAGE sql STABLE AS $$
  SELECT NULLIF(current_setting('app.user_id', true), '')::uuid;
$$;

CREATE OR REPLACE FUNCTION app.current_company_id() RETURNS uuid
LANGUAGE sql STABLE AS $$
  SELECT NULLIF(current_setting('app.company_id', true), '')::uuid;
$$;

CREATE OR REPLACE FUNCTION app.current_request_id() RETURNS text
LANGUAGE sql STABLE AS $$
  SELECT NULLIF(current_setting('app.request_id', true), '');
$$;

CREATE OR REPLACE FUNCTION app.actor_type() RETURNS text
LANGUAGE sql STABLE AS $$
  SELECT COALESCE(NULLIF(current_setting('app.actor_type', true), ''), 'user');
$$;

COMMENT ON FUNCTION app.current_user_id() IS
  'Authenticated user id for the current transaction. NULL means unauthenticated.';

-- -----------------------------------------------------------------------------
-- Trusted-context check.
--
-- Guard triggers (project creation, contract transitions, timesheet writes, ...)
-- enforce business rules for *request* traffic. Three contexts legitimately have
-- no end-user identity and must still be able to write:
--
--   * superuser            — migrations, seeds, Alembic, psql administration
--   * mytrakin_worker      — background ingestion (OCR, bank sync, index rebuild)
--   * RLS policy authors   — tests that assert isolation
--
-- The condition is deliberately narrow: it requires superuser OR BYPASSRLS.
-- `mytrakin_api` is neither, so a request can never enter this branch. There is
-- no configuration value and no user-controlled input that can widen it.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.is_trusted_context() RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT COALESCE(
    (SELECT r.rolsuper OR r.rolbypassrls FROM pg_roles r WHERE r.rolname = current_user),
    false);
$$;

COMMENT ON FUNCTION app.is_trusted_context() IS
  'True for superuser/BYPASSRLS roles (migrations, workers). Always false for mytrakin_api, so request traffic can never bypass guard triggers.';

-- -----------------------------------------------------------------------------
-- Immutability guard: public_id columns can never be changed after insert.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.freeze_public_id() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  -- Migrations, seeds and background workers have no end-user identity.
  -- mytrakin_api is neither superuser nor BYPASSRLS, so request traffic
  -- always falls through to the real checks below.
  IF app.is_trusted_context() THEN
    RETURN NEW;
  END IF;

  IF NEW.public_id IS DISTINCT FROM OLD.public_id THEN
    RAISE EXCEPTION 'public_id is immutable (%.public_id: % -> %)',
      TG_TABLE_NAME, OLD.public_id, NEW.public_id
      USING ERRCODE = 'restrict_violation';
  END IF;
  RETURN NEW;
END
$$;

-- -----------------------------------------------------------------------------
-- Generic updated_at maintenance
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.touch_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  -- Migrations, seeds and background workers have no end-user identity.
  -- mytrakin_api is neither superuser nor BYPASSRLS, so request traffic
  -- always falls through to the real checks below.
  IF app.is_trusted_context() THEN
    RETURN NEW;
  END IF;

  NEW.updated_at := now();
  RETURN NEW;
END
$$;

-- -----------------------------------------------------------------------------
-- Immutability guard for append-only ledgers (audit, usage, transactions)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.reject_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  -- Migrations, seeds and background workers have no end-user identity.
  -- mytrakin_api is neither superuser nor BYPASSRLS, so request traffic
  -- always falls through to the real checks below.
  IF app.is_trusted_context() THEN
    RETURN NEW;
  END IF;

  RAISE EXCEPTION '%. is append-only; % is not permitted', TG_TABLE_NAME, TG_OP
    USING ERRCODE = 'restrict_violation';
END
$$;

-- Reusable trigger installers
-- =============================================================================
-- Public-id tables get an immutability trigger plus an updated_at trigger.
-- attach_updated_at() adds only the updated_at trigger to a table that has no
-- public_id, so the two never collide on the same trigger name.
-- =============================================================================
CREATE OR REPLACE FUNCTION app.attach_triggers(p_table regclass) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
  -- regclass::text omits the schema when the relation is visible on the
  -- search_path, so resolve the bare name from pg_class instead of parsing it.
  v_name text;
BEGIN

  SELECT c.relname INTO v_name FROM pg_class c WHERE c.oid = p_table;

  IF v_name IS NULL THEN
    RAISE EXCEPTION 'app.attach_triggers: unknown relation %', p_table;
  END IF;

  -- PostgreSQL has no CREATE TRIGGER IF NOT EXISTS, so existence is checked in
  -- pg_trigger. This keeps migrations re-runnable without dropping triggers.
  IF NOT EXISTS (
    SELECT 1 FROM pg_trigger
     WHERE tgrelid = p_table AND tgname = 'trg_' || v_name || '_freeze_public_id'
       AND NOT tgisinternal
  ) THEN
    EXECUTE format(
      'CREATE TRIGGER %I BEFORE UPDATE ON %s FOR EACH ROW EXECUTE FUNCTION app.freeze_public_id()',
      'trg_' || v_name || '_freeze_public_id', p_table);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_trigger
     WHERE tgrelid = p_table AND tgname = 'trg_' || v_name || '_updated_at'
       AND NOT tgisinternal
  ) THEN
    EXECUTE format(
      'CREATE TRIGGER %I BEFORE UPDATE ON %s FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at()',
      'trg_' || v_name || '_updated_at', p_table);
  END IF;
END
$$;

COMMENT ON FUNCTION app.attach_triggers(regclass) IS
  'Installs the immutability + updated_at triggers for a table carrying a public_id column.';

-- Tables without a public_id still need updated_at maintenance.
CREATE OR REPLACE FUNCTION app.attach_updated_at(p_table regclass, p_trigger_name text) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN

  IF NOT EXISTS (SELECT 1 FROM pg_trigger
                  WHERE tgrelid = p_table AND tgname = p_trigger_name AND NOT tgisinternal) THEN
    EXECUTE format(
      'CREATE TRIGGER %I BEFORE UPDATE ON %s FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at()',
      p_trigger_name, p_table);
  END IF;
END
$$;

-- =============================================================================
-- Common enum types (created once; ADDed safely for repeatability)
-- =============================================================================
-- FOREACH cannot iterate a multi-dimensional array, so each definition is a
-- single 'name||values' string and split at run time.
DO $$
DECLARE
  t text;
  v_defs text[] := ARRAY[
    'membership_status|PENDING,ACTIVE,SUSPENDED,DEACTIVATED',
    'visibility|PUBLIC,CONNECTIONS,PRIVATE',
    'document_status|UPLOADING,SCANNING,PROCESSING,READY,QUARANTINED,REJECTED,EXPIRED,ARCHIVED',
    'contract_status|DRAFT,SENT,PENDING_ACCEPTANCE,ACCEPTED,ACTIVE,DECLINED,EXPIRED,TERMINATED,CLOSED',
    'sow_status|DRAFT,PENDING_APPROVAL,ACTIVE,EXPIRED,TERMINATED,CLOSED',
    'timesheet_status|DRAFT,SUBMITTED,UNDER_REVIEW,APPROVED,LOCKED,REJECTED',
    'invoice_status|DRAFT,PENDING,SUBMITTED,APPROVED,PARTIALLY_PAID,PAID,OVERDUE,REJECTED,DISPUTED,CANCELLED,REFUNDED',
    'invoice_direction|RECEIVABLE,PAYABLE',
    'payment_status|SCHEDULED,INITIATED,PROCESSING,COMPLETED,FAILED,PARTIALLY_REFUNDED,REFUNDED,CANCELLED',
    'leave_status|DRAFT,PENDING,APPROVED,REJECTED,CANCELLED',
    'billing_basis|TIMESHEET,FIXED,RECURRING,USAGE,MILESTONE',
    'billing_frequency|WEEKLY,BIWEEKLY,MONTHLY,QUARTERLY,CUSTOM',
    'line_item_kind|FIXED,RECURRING,USAGE,MILESTONE,TIMESHEET,VARIABLE,ADDITIONAL',
    'project_type|SERVICE,LENDING,BLUE_COLLAR',
    'project_category|COMPANY,INDIVIDUAL',
    'actor_type|USER,SYSTEM,AI,AGENT,INTEGRATION',
    'verification_state|UNVERIFIED,PENDING,VERIFIED,REJECTED,REQUIRES_REVIEW'
  ];
BEGIN
  FOREACH t IN ARRAY v_defs LOOP
    -- Quote each label: enum labels are identifiers, not bare words.
    EXECUTE format(
      'CREATE TYPE public.%I AS ENUM (%s)',
      split_part(t, '|', 1),
      (SELECT string_agg(quote_literal(l), ', ' ORDER BY o)
         FROM unnest(string_to_array(split_part(t, '|', 2), ',')) WITH ORDINALITY AS u(l, o)));
  END LOOP;
EXCEPTION
  WHEN duplicate_object THEN NULL;  -- already created by a previous run
END
$$;
