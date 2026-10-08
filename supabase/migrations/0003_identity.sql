-- =============================================================================
-- MyTrakin :: 0003_identity.sql
-- Users, profiles, privacy controls, professional credentials, skill taxonomy.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Public ID assignment for users (must exist before the users trigger).
-- Retry loop guards against the (astronomically unlikely) base32 collision; the
-- UNIQUE constraint remains the final arbiter.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.assign_user_public_id() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  v_try int := 0;
  v_id  text;
BEGIN

  LOOP
    v_id := app.gen_public_id('U', 8);
    EXIT WHEN NOT EXISTS (SELECT 1 FROM public.users WHERE public_id = v_id);
    v_try := v_try + 1;
    IF v_try > 5 THEN
      RAISE EXCEPTION 'could not allocate a unique user public_id after % attempts', v_try;
    END IF;
  END LOOP;
  NEW.public_id := v_id;
  RETURN NEW;
END
$$;

-- -----------------------------------------------------------------------------
-- users — mirrors the Supabase Auth account (auth_id) with a platform identity.
-- auth_id is the link; public_id is what humans and URLs use.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.users (
  id                uuid PRIMARY KEY DEFAULT app.uuid7(),
  auth_id           uuid NOT NULL UNIQUE,
  public_id         text NOT NULL UNIQUE,
  email             citext NOT NULL,
  email_normalized  text GENERATED ALWAYS AS (lower(email::text)) STORED,
  email_verified_at timestamptz,
  first_name        text NOT NULL,
  last_name         text NOT NULL,
  phone_e164        text,
  country_code      char(2),
  date_of_birth     date,
  avatar_url        text,
  status            text NOT NULL DEFAULT 'ACTIVE'
                      CHECK (status IN ('ACTIVE', 'PENDING_VERIFICATION', 'SUSPENDED', 'DEACTIVATED')),
  locale            text NOT NULL DEFAULT 'en',
  default_currency  char(3) NOT NULL DEFAULT 'USD',
  default_visibility public.visibility NOT NULL DEFAULT 'PUBLIC',
  -- Per-category notification preferences, keyed by category:
  -- CONTRACT, PROJECT, SOW, MSA, TIMESHEET, INVOICE, PAYMENT, LEAVE, MESSAGE,
  -- CONNECTION, AI, SECURITY, COMPLIANCE, SYSTEM.
  -- AI categories default to email=false: opt-in, not opt-out.
  notification_preferences jsonb NOT NULL DEFAULT '{}'::jsonb,
  -- Explicit AI opt-in. Every capability defaults to disabled.
  ai_settings       jsonb NOT NULL DEFAULT
                      '{"assistant_enabled":false,"document_ai_enabled":false,
                        "automations_enabled":false,"allow_training_use":false}'::jsonb,
  privacy_settings  jsonb NOT NULL DEFAULT '{}'::jsonb,
  security_settings jsonb NOT NULL DEFAULT
                      '{"mfa_required":false,"login_alerts":true,"session_timeout_minutes":10080}'::jsonb,
  onboarding_completed_at timestamptz,
  last_login_at     timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  deleted_at        timestamptz,
  CONSTRAINT ck_user_public_id CHECK (public_id ~ '^U[0-9A-HJKMNP-TV-Z]{8}$'),
  CONSTRAINT ck_user_email_verified_status
    CHECK (status <> 'ACTIVE' OR email_verified_at IS NOT NULL)
);

COMMENT ON COLUMN public.users.auth_id IS 'Supabase Auth user id (auth.users.id). Immutable.';
COMMENT ON COLUMN public.users.public_id IS 'Immutable public identifier, e.g. U8K29F4A7.';
COMMENT ON COLUMN public.users.email_normalized IS
  'Generated: lowercase email for unique-case-insensitive login and dedupe.';
COMMENT ON COLUMN public.users.notification_preferences IS
  'Per-category in-app/email/push toggles. AI categories are opt-in by default.';
COMMENT ON COLUMN public.users.ai_settings IS
  'Explicit AI consent switches. Absent/false means the capability is unavailable to this user.';

CREATE UNIQUE INDEX IF NOT EXISTS ux_users_email ON public.users (email_normalized)
  WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_users_status_created ON public.users (status, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_users_name_trgm
  ON public.users USING gin ((first_name || ' ' || last_name) gin_trgm_ops);

SELECT app.attach_triggers('public.users'::regclass);
DROP TRIGGER IF EXISTS trg_users_public_id ON public.users;
CREATE TRIGGER trg_users_public_id BEFORE INSERT ON public.users
  FOR EACH ROW WHEN (NEW.public_id IS NULL)
  EXECUTE FUNCTION app.assign_user_public_id();

-- -----------------------------------------------------------------------------
-- user_profiles — everything a user chooses to present professionally.
-- Split from `users` so that sensitive identity fields can live under a
-- different RLS policy than public profile data.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.user_profiles (
  user_id             uuid PRIMARY KEY REFERENCES public.users(id) ON DELETE CASCADE,
  public_id           text NOT NULL UNIQUE,
  headline            text,
  bio                 text,
  location_city       text,
  location_region     text,
  location_country    char(2),
  timezone            text NOT NULL DEFAULT 'UTC',
  current_company_id  uuid,
  years_experience    numeric(4,1) CHECK (years_experience IS NULL OR years_experience >= 0),
  availability_status text NOT NULL DEFAULT 'NOT_LOOKING'
                        CHECK (availability_status IN ('NOT_LOOKING','OPEN','ACTIVELY_LOOKING','NOT_AVAILABLE')),
  languages           text[] NOT NULL DEFAULT '{}',
  interests           text[] NOT NULL DEFAULT '{}',
  profile_visibility  public.visibility NOT NULL DEFAULT 'PUBLIC',
  default_visibility  public.visibility NOT NULL DEFAULT 'PUBLIC',
  linkedin_url        text,
  website_url         text,
  updated_at          timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_profile_public_id CHECK (public_id ~ '^U[0-9A-HJKMNP-TV-Z]{8}$')
);

COMMENT ON TABLE public.user_profiles IS
  'Public-facing professional profile. Separate from users so profile reads never expose credential-adjacent fields.';
COMMENT ON COLUMN public.user_profiles.profile_visibility IS
  'Who may see this profile at all when no accepted connection exists.';

-- The profile mirrors the user's public identifier so profile URLs need only one id.
CREATE OR REPLACE FUNCTION app.sync_profile_public_id() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN

  IF NEW.public_id IS NULL THEN
    SELECT u.public_id INTO NEW.public_id FROM public.users u WHERE u.id = NEW.user_id;
  END IF;
  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_profile_public_id ON public.user_profiles;
CREATE TRIGGER trg_profile_public_id BEFORE INSERT ON public.user_profiles
  FOR EACH ROW EXECUTE FUNCTION app.sync_profile_public_id();

-- -----------------------------------------------------------------------------
-- user_privacy — per-field visibility overrides.
-- PUBLIC | CONNECTIONS (accepted connections only) | PRIVATE (self only)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.user_privacy (
  user_id    uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  field_path text NOT NULL,          -- 'headline', 'contact.email', 'location', 'experience', ...
  visibility public.visibility NOT NULL DEFAULT 'CONNECTIONS',
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, field_path)
);

COMMENT ON TABLE public.user_privacy IS
  'Field-level visibility control. Applied server-side before any profile payload is assembled.';

-- -----------------------------------------------------------------------------
-- user_sensitive — the protected store. Never joined into profile responses.
-- SSN / tax identifiers are stored encrypted-at-rest by the database provider and
-- are additionally envelope-encrypted by the API when SENSITIVE_DATA_ENCRYPTION_KEY
-- is configured. Only a redacted projection ever leaves this table.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.user_sensitive (
  user_id          uuid PRIMARY KEY REFERENCES public.users(id) ON DELETE CASCADE,
  ssn_last4        char(4),
  ssn_encrypted    bytea,
  tax_id_last4     char(4),
  tax_id_encrypted bytea,
  tax_id_type      text CHECK (tax_id_type IS NULL OR tax_id_type IN ('EIN','SSN','ITIN','UNKNOWN')),
  bank_token_ref   text,             -- opaque reference to a token vault; never a bank credential
  verification_state public.verification_state NOT NULL DEFAULT 'UNVERIFIED',
  verified_at      timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.user_sensitive IS
  'Protected personal data. Self-access only; masked in all API responses; excluded from logs, analytics and AI context.';

-- -----------------------------------------------------------------------------
-- Skill taxonomy + user skills
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.skills (
  id         uuid PRIMARY KEY DEFAULT app.uuid7(),
  name       text NOT NULL UNIQUE,
  slug       text GENERATED ALWAYS AS (lower(regexp_replace(name, '[^a-zA-Z0-9]+', '-', 'g'))) STORED,
  category   text,
  is_active  boolean NOT NULL DEFAULT true
);

CREATE INDEX IF NOT EXISTS ix_skills_slug ON public.skills (slug);
CREATE INDEX IF NOT EXISTS ix_skills_name_trgm ON public.skills USING gin (name gin_trgm_ops);

CREATE TABLE IF NOT EXISTS public.user_skills (
  user_id     uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  skill_id    uuid NOT NULL REFERENCES public.skills(id) ON DELETE CASCADE,
  proficiency smallint NOT NULL DEFAULT 3 CHECK (proficiency BETWEEN 1 AND 5),
  years_experience numeric(3,1) CHECK (years_experience IS NULL OR years_experience >= 0),
  endorsed_by uuid REFERENCES public.users(id) ON DELETE SET NULL,
  visible     boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, skill_id)
);

CREATE INDEX IF NOT EXISTS ix_user_skills_skill ON public.user_skills (skill_id) WHERE visible;

-- -----------------------------------------------------------------------------
-- Education and career history
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.user_educations (
  id            uuid PRIMARY KEY DEFAULT app.uuid7(),
  user_id       uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  institution   text NOT NULL,
  degree        text,
  field_of_study text,
  start_date    date,
  end_date      date,
  grade         text,
  description   text,
  credential_id text,
  is_verified   boolean NOT NULL DEFAULT false,
  visible       boolean NOT NULL DEFAULT true,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_edu_dates CHECK (end_date IS NULL OR start_date IS NULL OR end_date >= start_date)
);

CREATE INDEX IF NOT EXISTS ix_edu_user ON public.user_educations (user_id, end_date DESC NULLS LAST);

CREATE TABLE IF NOT EXISTS public.user_experiences (
  id             uuid PRIMARY KEY DEFAULT app.uuid7(),
  user_id        uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  company_id     uuid,                        -- set when the employer is a platform company
  company_name   text NOT NULL,               -- free text, so external employers still record
  title          text NOT NULL,
  employment_type text CHECK (employment_type IS NULL
                              OR employment_type IN ('FULL_TIME','PART_TIME','CONTRACT','CONSULTANT','INTERN')),
  location       text,
  description    text,
  start_date     date NOT NULL,
  end_date       date,
  is_current     boolean NOT NULL DEFAULT false,
  visible        boolean NOT NULL DEFAULT true,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_exp_dates CHECK (end_date IS NULL OR end_date >= start_date)
);

CREATE INDEX IF NOT EXISTS ix_exp_user ON public.user_experiences (user_id, start_date DESC);
CREATE INDEX IF NOT EXISTS ix_exp_company ON public.user_experiences (company_id) WHERE company_id IS NOT NULL;

-- -----------------------------------------------------------------------------
-- Certifications
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.user_certifications (
  id             uuid PRIMARY KEY DEFAULT app.uuid7(),
  user_id        uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  name           text NOT NULL,
  issuer         text NOT NULL,
  issued_on      date,
  expires_on     date,
  credential_id  text,
  document_id    uuid,                        -- FK added in 0006 (documents)
  visible        boolean NOT NULL DEFAULT true,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_cert_user ON public.user_certifications (user_id);
CREATE INDEX IF NOT EXISTS ix_cert_expiry ON public.user_certifications (expires_on)
  WHERE expires_on IS NOT NULL;

-- -----------------------------------------------------------------------------
-- Rate-limit / abuse markers on accounts
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.user_security_flags (
  id          uuid PRIMARY KEY DEFAULT app.uuid7(),
  user_id     uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  flag_type   text NOT NULL,
  severity    text NOT NULL DEFAULT 'INFO' CHECK (severity IN ('INFO','WARNING','CRITICAL')),
  details     jsonb NOT NULL DEFAULT '{}'::jsonb,
  resolved_at timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_security_flags_open
  ON public.user_security_flags (user_id, created_at DESC) WHERE resolved_at IS NULL;

GRANT USAGE ON SCHEMA public TO mytrakin_api, mytrakin_worker, mytrakin_readonly;
GRANT SELECT, INSERT, UPDATE, DELETE ON
  public.users, public.user_profiles, public.user_privacy, public.skills,
  public.user_skills, public.user_educations, public.user_experiences,
  public.user_certifications, public.user_security_flags
TO mytrakin_api, mytrakin_worker;
GRANT SELECT ON public.skills TO mytrakin_readonly;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.user_sensitive TO mytrakin_api, mytrakin_worker;
