-- =============================================================================
-- MyTrakin :: 0006_documents_msa.sql
-- Secure document repository (versioning, retention, signed-URL access log) and
-- Master Service Agreements between companies.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- documents — logical document. Physical bytes live in a private Supabase
-- Storage bucket; only short-lived signed URLs are ever issued to clients.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.documents (
  id            uuid PRIMARY KEY DEFAULT app.uuid7(),
  public_id     text NOT NULL UNIQUE,
  company_id    uuid REFERENCES public.companies(id) ON DELETE CASCADE,
  owner_user_id uuid REFERENCES public.users(id) ON DELETE SET NULL,
  doc_type      text NOT NULL,
  title         text NOT NULL,
  description   text,
  visibility    public.visibility NOT NULL DEFAULT 'CONNECTIONS',
  current_version_id uuid,
  version_count int NOT NULL DEFAULT 0,
  status        public.document_status NOT NULL DEFAULT 'UPLOADING',
  is_legal_hold boolean NOT NULL DEFAULT false,   -- blocks deletion under retention
  retention_until date,
  retention_policy text NOT NULL DEFAULT 'STANDARD_7Y',
  related_type  text,                            -- polymorphic link, validated by API
  related_id    uuid,
  checksum_sha256 char(64),
  ai_processing_state text NOT NULL DEFAULT 'NOT_REQUESTED',
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  deleted_at    timestamptz
);

COMMENT ON TABLE public.documents IS
  'Logical document. Bytes in a private bucket; access always via short-lived signed URLs.';
COMMENT ON COLUMN public.documents.retention_until IS
  'Legal/financial retention. Combined with is_legal_hold this prevents silent deletion of contracts.';

CREATE INDEX IF NOT EXISTS ix_documents_company
  ON public.documents (company_id, created_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_documents_related
  ON public.documents (related_type, related_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_documents_type
  ON public.documents (company_id, doc_type, created_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_documents_expiry
  ON public.documents (retention_until) WHERE retention_until IS NOT NULL AND deleted_at IS NULL;

CREATE OR REPLACE FUNCTION app.assign_document_public_id() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_try int := 0; v_id text;
BEGIN

  LOOP
    v_id := app.gen_public_id('D', 8);
    EXIT WHEN NOT EXISTS (SELECT 1 FROM public.documents WHERE public_id = v_id);
    v_try := v_try + 1;
    IF v_try > 5 THEN RAISE EXCEPTION 'could not allocate document public_id'; END IF;
  END LOOP;
  NEW.public_id := v_id;
  RETURN NEW;
END
$$;

SELECT app.attach_triggers('public.documents'::regclass);
DROP TRIGGER IF EXISTS trg_documents_public_id ON public.documents;
CREATE TRIGGER trg_documents_public_id BEFORE INSERT ON public.documents
  FOR EACH ROW WHEN (NEW.public_id IS NULL)
  EXECUTE FUNCTION app.assign_document_public_id();

-- -----------------------------------------------------------------------------
-- document_versions — immutable. Legal documents are never overwritten.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.document_versions (
  id            uuid PRIMARY KEY DEFAULT app.uuid7(),
  document_id   uuid NOT NULL REFERENCES public.documents(id) ON DELETE CASCADE,
  version_no    int NOT NULL CHECK (version_no > 0),
  storage_bucket text NOT NULL,
  storage_path  text NOT NULL,
  file_name     text NOT NULL,
  mime_type     text NOT NULL,
  byte_size     bigint NOT NULL CHECK (byte_size > 0),
  checksum_sha256 char(64) NOT NULL,
  encryption_key_ref text,
  uploaded_by   uuid REFERENCES public.users(id) ON DELETE SET NULL,
  upload_ip     inet,
  scan_status   text NOT NULL DEFAULT 'PENDING'
                  CHECK (scan_status IN ('PENDING','CLEAN','INFECTED','ERROR','SKIPPED')),
  scanned_at    timestamptz,
  scan_engine   text,
  scan_detail   jsonb,
  extracted_text text,
  extraction_state text NOT NULL DEFAULT 'NOT_STARTED',
  ocr_confidence numeric(5,4),
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (document_id, version_no),
  CONSTRAINT ck_version_extracted_len CHECK (extracted_text IS NULL OR length(extracted_text) <= 50000000)
);

COMMENT ON COLUMN public.document_versions.scan_status IS
  'Malware scan gate. Documents in PENDING/INFECTED/ERROR may not be downloaded or ingested by AI.';
COMMENT ON COLUMN public.document_versions.storage_path IS
  'Path inside the private bucket. Never a public URL.';

CREATE INDEX IF NOT EXISTS ix_docver_document ON public.document_versions (document_id, version_no DESC);
CREATE INDEX IF NOT EXISTS ix_docver_scan     ON public.document_versions (scan_status) WHERE scan_status = 'PENDING';
CREATE UNIQUE INDEX IF NOT EXISTS ux_docver_checksum
  ON public.document_versions (checksum_sha256, document_id);

-- Versions are immutable once written.
DROP TRIGGER IF EXISTS trg_docver_immutable ON public.document_versions;
CREATE TRIGGER trg_docver_immutable BEFORE UPDATE OR DELETE ON public.document_versions
  FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

-- Keep documents.version_count / current_version_id truthful.
CREATE OR REPLACE FUNCTION app.sync_document_version() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, app, pg_temp
AS $$
DECLARE v_doc uuid := NEW.document_id;
BEGIN

  UPDATE public.documents d SET
    version_count = (SELECT count(*) FROM public.document_versions v WHERE v.document_id = v_doc),
    current_version_id = (SELECT v.id FROM public.document_versions v
                           WHERE v.document_id = v_doc ORDER BY v.version_no DESC LIMIT 1),
    status = CASE
      WHEN EXISTS (SELECT 1 FROM public.document_versions v
                    WHERE v.document_id = v_doc AND v.scan_status = 'INFECTED')
        THEN 'QUARANTINED'::public.document_status
      ELSE 'READY'::public.document_status END,
    checksum_sha256 = (SELECT v.checksum_sha256 FROM public.document_versions v
                        WHERE v.document_id = v_doc ORDER BY v.version_no DESC LIMIT 1)
   WHERE d.id = v_doc;
  RETURN NULL;
END
$$;

DROP TRIGGER IF EXISTS trg_docver_sync ON public.document_versions;
CREATE TRIGGER trg_docver_sync AFTER INSERT ON public.document_versions
  FOR EACH ROW EXECUTE FUNCTION app.sync_document_version();

-- Uploaded files are immutable: only the owner or a documents.upload holder may add versions.
CREATE OR REPLACE FUNCTION app.assert_document_upload() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_company uuid; v_doc_type text;
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

  SELECT company_id, doc_type INTO v_company, v_doc_type FROM public.documents WHERE id = NEW.document_id;

  IF v_company IS NOT NULL THEN
    IF NEW.uploaded_by IS DISTINCT FROM app.current_user_id()
       AND NOT app.has_permission(v_company, 'documents.upload') THEN
      RAISE EXCEPTION 'missing permission documents.upload' USING ERRCODE = 'insufficient_privilege';
    END IF;
  END IF;

  IF v_doc_type = 'W9' AND v_company IS NOT NULL
     AND NOT app.has_permission(v_company, 'documents.upload')
     AND NOT app.has_permission(v_company, 'companies.update') THEN
    RAISE EXCEPTION 'W-9 upload requires documents.upload' USING ERRCODE = 'insufficient_privilege';
  END IF;

  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_docver_guard ON public.document_versions;
CREATE TRIGGER trg_docver_guard BEFORE INSERT ON public.document_versions
  FOR EACH ROW EXECUTE FUNCTION app.assert_document_upload();

-- -----------------------------------------------------------------------------
-- document_access_log — who opened what. Required for confidential documents.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.document_access_log (
  id           uuid PRIMARY KEY DEFAULT app.uuid7(),
  document_id  uuid NOT NULL REFERENCES public.documents(id) ON DELETE CASCADE,
  version_id   uuid REFERENCES public.document_versions(id) ON DELETE SET NULL,
  user_id      uuid REFERENCES public.users(id) ON DELETE SET NULL,
  company_id   uuid,
  access_type  text NOT NULL CHECK (access_type IN ('VIEW','DOWNLOAD','SIGNED_URL','PREVIEW','PRINT')),
  purpose      text,
  ip_address   inet,
  user_agent   text,
  request_id   text,
  accessed_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_docaccess_doc ON public.document_access_log (document_id, accessed_at DESC);
CREATE INDEX IF NOT EXISTS ix_docaccess_user ON public.document_access_log (user_id, accessed_at DESC);

-- Retention/hold guard.
CREATE OR REPLACE FUNCTION app.assert_document_deletable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  -- Migrations, seeds and background workers have no end-user identity.
  -- mytrakin_api is neither superuser nor BYPASSRLS, so request traffic
  -- always falls through to the real checks below.
  IF app.is_trusted_context() THEN
    RETURN NEW;
  END IF;

  IF OLD.is_legal_hold THEN
    RAISE EXCEPTION 'document % is under legal hold and cannot be deleted', OLD.public_id
      USING ERRCODE = 'restrict_violation';
  END IF;
  IF OLD.retention_until IS NOT NULL AND OLD.retention_until > now() THEN
    RAISE EXCEPTION 'document % is retained until %', OLD.public_id, OLD.retention_until
      USING ERRCODE = 'restrict_violation';
  END IF;
  RETURN OLD;
END
$$;

DROP TRIGGER IF EXISTS trg_documents_hold ON public.documents;
CREATE TRIGGER trg_documents_hold BEFORE DELETE ON public.documents
  FOR EACH ROW EXECUTE FUNCTION app.assert_document_deletable();

-- Defer the FKs declared in earlier migrations now that documents exists.
ALTER TABLE public.companies
  DROP CONSTRAINT IF EXISTS companies_w9_document_id_fkey;
ALTER TABLE public.companies
  ADD  CONSTRAINT companies_w9_document_id_fkey
  FOREIGN KEY (w9_document_id) REFERENCES public.documents(id) ON DELETE SET NULL;

ALTER TABLE public.posts DROP CONSTRAINT IF EXISTS posts_document_id_fkey;
ALTER TABLE public.posts
  ADD CONSTRAINT posts_document_id_fkey
  FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE SET NULL;

ALTER TABLE public.messages DROP CONSTRAINT IF EXISTS messages_document_id_fkey;
ALTER TABLE public.messages
  ADD CONSTRAINT messages_document_id_fkey
  FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE SET NULL;

ALTER TABLE public.user_certifications DROP CONSTRAINT IF EXISTS user_certifications_document_id_fkey;
ALTER TABLE public.user_certifications
  ADD CONSTRAINT user_certifications_document_id_fkey
  FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE SET NULL;

-- =============================================================================
-- Master Service Agreements
-- One row per ordered company pair; both directions share the same agreement.
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.msas (
  id             uuid PRIMARY KEY DEFAULT app.uuid7(),
  public_id      text NOT NULL UNIQUE,
  -- deterministic pair key so A->B and B->A resolve to one agreement
  pair_low       uuid GENERATED ALWAYS AS (LEAST(company_a_id, company_b_id)) STORED,
  pair_high      uuid GENERATED ALWAYS AS (GREATEST(company_a_id, company_b_id)) STORED,
  company_a_id   uuid NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
  company_b_id   uuid NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
  status         text NOT NULL DEFAULT 'NO_MSA'
                   CHECK (status IN ('NO_MSA','MSA_REQUESTED','MSA_SUBMITTED','UNDER_REVIEW',
                                     'ACTIVE','REJECTED','EXPIRED','TERMINATED')),
  effective_date date,
  expiration_date date,
  auto_renew     boolean NOT NULL DEFAULT false,
  renewal_notice_days int CHECK (renewal_notice_days IS NULL OR renewal_notice_days > 0),
  governing_law  text,
  payment_terms_days int NOT NULL DEFAULT 30 CHECK (payment_terms_days >= 0),
  current_version_id uuid,
  requested_by   uuid REFERENCES public.users(id) ON DELETE SET NULL,
  reviewed_by    uuid REFERENCES public.users(id) ON DELETE SET NULL,
  activated_at   timestamptz,
  terminated_at  timestamptz,
  notes          text,
  metadata       jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (pair_low, pair_high),
  CONSTRAINT ck_msa_distinct_companies CHECK (company_a_id <> company_b_id),
  CONSTRAINT ck_msa_dates CHECK (expiration_date IS NULL OR effective_date IS NULL
                                 OR expiration_date >= effective_date)
);

COMMENT ON TABLE public.msas IS
  'Master Service Agreement between two companies. Required before an invoice may be SUBMITTED between them.';
COMMENT ON COLUMN public.msas.pair_low IS
  'Normalised pair key: one agreement per company pair regardless of direction.';

CREATE INDEX IF NOT EXISTS ix_msa_company_a ON public.msas (company_a_id, status);
CREATE INDEX IF NOT EXISTS ix_msa_company_b ON public.msas (company_b_id, status);
CREATE INDEX IF NOT EXISTS ix_msa_expiring   ON public.msas (expiration_date)
  WHERE status = 'ACTIVE' AND expiration_date IS NOT NULL;

CREATE OR REPLACE FUNCTION app.assign_msa_public_id() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_try int := 0; v_id text;
BEGIN

  LOOP
    v_id := app.gen_public_id('M', 8);
    EXIT WHEN NOT EXISTS (SELECT 1 FROM public.msas WHERE public_id = v_id);
    v_try := v_try + 1;
    IF v_try > 5 THEN RAISE EXCEPTION 'could not allocate msa public_id'; END IF;
  END LOOP;
  NEW.public_id := v_id;
  RETURN NEW;
END
$$;

SELECT app.attach_triggers('public.msas'::regclass);
DROP TRIGGER IF EXISTS trg_msas_public_id ON public.msas;
CREATE TRIGGER trg_msas_public_id BEFORE INSERT ON public.msas
  FOR EACH ROW WHEN (NEW.public_id IS NULL)
  EXECUTE FUNCTION app.assign_msa_public_id();

-- Only counterparty members may read an MSA; this powers the invoice gate.
CREATE OR REPLACE FUNCTION app.msa_between(p_a uuid, p_b uuid) RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, app, pg_temp
AS $$
  SELECT m.id FROM public.msas m
   WHERE m.pair_low = LEAST(p_a, p_b) AND m.pair_high = GREATEST(p_a, p_b);
$$;

-- TRUE when an ACTIVE MSA exists between two companies: the invoice eligibility gate.
CREATE OR REPLACE FUNCTION app.has_active_msa(p_a uuid, p_b uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, app, pg_temp
AS $$
  SELECT COALESCE((
    SELECT m.status = 'ACTIVE'
       AND (m.expiration_date IS NULL OR m.expiration_date > current_date)
      FROM public.msas m
     WHERE m.pair_low = LEAST(p_a, p_b) AND m.pair_high = GREATEST(p_a, p_b)
  ), false);
$$;

COMMENT ON FUNCTION app.has_active_msa(uuid, uuid) IS
  'Invoice submission gate: invoices may exist without an MSA but must stay DRAFT until one is ACTIVE.';

CREATE OR REPLACE FUNCTION app.assert_msa_transition() RETURNS trigger
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
  IF NEW.status IS DISTINCT FROM OLD.status THEN
    IF NEW.status IN ('ACTIVE','UNDER_REVIEW','REJECTED') THEN
      IF NOT (app.is_member(NEW.company_a_id) OR app.is_member(NEW.company_b_id)) THEN
        RAISE EXCEPTION 'only a member of a party company may set msa status to %', NEW.status
          USING ERRCODE = 'insufficient_privilege';
      END IF;
    END IF;

    IF NEW.status = 'ACTIVE' THEN
      IF NEW.effective_date IS NULL OR NEW.expiration_date IS NULL THEN
        RAISE EXCEPTION 'an ACTIVE msa requires effective_date and expiration_date'
          USING ERRCODE = 'check_violation';
      END IF;
      NEW.activated_at := COALESCE(OLD.activated_at, now());
    END IF;

    IF NEW.status = 'TERMINATED' THEN
      NEW.terminated_at := COALESCE(OLD.terminated_at, now());
    END IF;
  END IF;
  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_msa_transition ON public.msas;
CREATE TRIGGER trg_msa_transition BEFORE UPDATE ON public.msas
  FOR EACH ROW EXECUTE FUNCTION app.assert_msa_transition();

-- -----------------------------------------------------------------------------
-- msa_versions — immutable version chain of the executed document.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.msa_versions (
  id           uuid PRIMARY KEY DEFAULT app.uuid7(),
  msa_id       uuid NOT NULL REFERENCES public.msas(id) ON DELETE CASCADE,
  version_no   int NOT NULL CHECK (version_no > 0),
  document_version_id uuid REFERENCES public.document_versions(id) ON DELETE SET NULL,
  status       text NOT NULL DEFAULT 'DRAFT'
                 CHECK (status IN ('DRAFT','SUBMITTED','UNDER_REVIEW','ACCEPTED','REJECTED','SUPERSEDED')),
  submitted_by uuid REFERENCES public.users(id) ON DELETE SET NULL,
  submitted_at timestamptz,
  reviewed_by  uuid REFERENCES public.users(id) ON DELETE SET NULL,
  reviewed_at  timestamptz,
  review_notes text,
  effective_date date,
  expiration_date date,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (msa_id, version_no)
);

CREATE INDEX IF NOT EXISTS ix_msaver_pending
  ON public.msa_versions (msa_id, created_at DESC) WHERE status IN ('SUBMITTED','UNDER_REVIEW');

DROP TRIGGER IF EXISTS trg_msaver_immutable ON public.msa_versions;
CREATE TRIGGER trg_msaver_immutable BEFORE DELETE ON public.msa_versions
  FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

CREATE OR REPLACE FUNCTION app.sync_msa_current_version() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, app, pg_temp
AS $$
DECLARE v_msa uuid := NEW.msa_id;
BEGIN

  UPDATE public.msas m
     SET current_version_id = (
           SELECT v.id FROM public.msa_versions v
            WHERE v.msa_id = v_msa ORDER BY v.version_no DESC LIMIT 1),
         effective_date = COALESCE(m.effective_date,
           (SELECT v.effective_date FROM public.msa_versions v
             WHERE v.msa_id = v_msa AND v.status = 'ACCEPTED'
             ORDER BY v.version_no DESC LIMIT 1)),
         expiration_date = COALESCE(m.expiration_date,
           (SELECT v.expiration_date FROM public.msa_versions v
             WHERE v.msa_id = v_msa AND v.status = 'ACCEPTED'
             ORDER BY v.version_no DESC LIMIT 1))
   WHERE m.id = v_msa;
  RETURN NULL;
END
$$;

DROP TRIGGER IF EXISTS trg_msaver_sync ON public.msa_versions;
CREATE TRIGGER trg_msaver_sync AFTER INSERT OR UPDATE ON public.msa_versions
  FOR EACH ROW EXECUTE FUNCTION app.sync_msa_current_version();

-- -----------------------------------------------------------------------------
-- msa_requests — the "we have no MSA, please sign" workflow.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.msa_requests (
  id            uuid PRIMARY KEY DEFAULT app.uuid7(),
  msa_id        uuid NOT NULL REFERENCES public.msas(id) ON DELETE CASCADE,
  requester_company_id uuid NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
  target_company_id     uuid NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
  requested_by  uuid REFERENCES public.users(id) ON DELETE SET NULL,
  message       text,
  template_key  text,
  status        text NOT NULL DEFAULT 'PENDING'
                  CHECK (status IN ('PENDING','ACCEPTED','DECLINED','CANCELLED','EXPIRED')),
  responded_by  uuid REFERENCES public.users(id) ON DELETE SET NULL,
  responded_at  timestamptz,
  response_notes text,
  expires_at    timestamptz NOT NULL DEFAULT (now() + interval '30 days'),
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (msa_id, requester_company_id, target_company_id, status)
);

CREATE INDEX IF NOT EXISTS ix_msareq_target
  ON public.msa_requests (target_company_id, created_at DESC) WHERE status = 'PENDING';

-- MSA state changes emit events for notifications and invoice re-evaluation.
CREATE OR REPLACE FUNCTION app.emit_msa_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, app, platform, pg_temp
AS $$
BEGIN
  IF NEW.status IS DISTINCT FROM OLD.status THEN
    INSERT INTO platform.outbox_events (
      event_type, company_id, aggregate_type, aggregate_id, payload, idempotency_key
    ) VALUES (
      'MSA_STATUS_CHANGED', NEW.company_a_id, 'msa', NEW.id,
      jsonb_build_object(
        'old_status', OLD.status, 'new_status', NEW.status,
        'company_a_id', NEW.company_a_id, 'company_b_id', NEW.company_b_id),
      'msa_status:' || NEW.id || ':' || NEW.status
    );
  END IF;
  RETURN NULL;
END
$$;

DROP TRIGGER IF EXISTS trg_msa_event ON public.msas;
CREATE TRIGGER trg_msa_event AFTER UPDATE ON public.msas
  FOR EACH ROW EXECUTE FUNCTION app.emit_msa_event();

GRANT EXECUTE ON FUNCTION app.has_active_msa(uuid, uuid) TO mytrakin_api, mytrakin_worker;
GRANT EXECUTE ON FUNCTION app.msa_between(uuid, uuid) TO mytrakin_api, mytrakin_worker;

GRANT SELECT, INSERT, UPDATE, DELETE ON
  public.documents, public.document_versions, public.document_access_log,
  public.msas, public.msa_versions, public.msa_requests
TO mytrakin_api, mytrakin_worker;
