-- =============================================================================
-- MyTrakin :: 0014_global_search.sql
-- Permission-aware global search.
--
-- Design: PostgreSQL full-text search + trigram first. The interface
-- (app.search_all) is deliberately narrow and documented so an OpenSearch or
-- Elasticsearch backend can be introduced later without changing callers: the
-- function keeps the same signature and returns the same row shape.
--
-- Every branch of the search applies the caller's permissions. There is no
-- "search everything then filter in the app" path.
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.search_index (
  -- Materialised, denormalised projection of searchable entities. Keeping this
  -- separate lets us add ranking signals later without touching domain tables.
  id            uuid PRIMARY KEY DEFAULT app.uuid7(),
  entity_type   text NOT NULL,
  entity_id     uuid NOT NULL,
  company_id    uuid,
  public_id     text NOT NULL,
  title         text NOT NULL,
  subtitle      text,
  body          text,
  entity_tsv    tsvector GENERATED ALWAYS AS (
                  setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
                  setweight(to_tsvector('english', coalesce(subtitle, '')), 'B') ||
                  setweight(to_tsvector('english', coalesce(body, '')), 'C')
                ) STORED,
  entity_trgm   text GENERATED ALWAYS AS (coalesce(title, '') || ' ' || coalesce(subtitle, '')) STORED,
  required_permission text NOT NULL DEFAULT 'dashboard.read',
  visibility    public.visibility NOT NULL DEFAULT 'PUBLIC',
  is_active     boolean NOT NULL DEFAULT true,
  popularity    numeric(10,4) NOT NULL DEFAULT 0,
  updated_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (entity_type, entity_id)
);

COMMENT ON TABLE public.search_index IS
  'Search projection. Rebuild with app.reindex_entity(); only the worker role writes here.';

CREATE INDEX IF NOT EXISTS ix_search_tsv  ON public.search_index USING gin (entity_tsv);
CREATE INDEX IF NOT EXISTS ix_search_trgm ON public.search_index USING gin (entity_trgm gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_search_company ON public.search_index (company_id, entity_type);
CREATE INDEX IF NOT EXISTS ix_search_active  ON public.search_index (entity_type, popularity DESC)
  WHERE is_active;

-- RLS: a row is visible only inside its company, and only with its permission.
ALTER TABLE public.search_index ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS search_index_read ON public.search_index;
CREATE POLICY search_index_read ON public.search_index FOR SELECT
  USING (
    is_active
    AND (
      -- cross-company public entities (people, companies, public posts)
      (visibility = 'PUBLIC' AND company_id IS NULL)
      OR (company_id IS NOT NULL
          AND company_id = app.current_company_id()
          AND app.has_permission(company_id, required_permission))
    )
  );

-- Only the worker writes the index: no INSERT/UPDATE/DELETE policy for mytrakin_api.

-- -----------------------------------------------------------------------------
-- Rebuild helpers
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.reindex_entity(
  p_entity_type text, p_entity_id uuid
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, app, pg_temp
AS $$
BEGIN

  DELETE FROM public.search_index WHERE entity_type = p_entity_type AND entity_id = p_entity_id;

  CASE p_entity_type
    WHEN 'PERSON' THEN
      -- COALESCE matters: the users insert trigger fires before the matching
      -- user_profiles row exists, so profile_visibility is NULL at that moment.
      INSERT INTO public.search_index
        (entity_type, entity_id, public_id, title, subtitle, body, required_permission, visibility)
      SELECT 'PERSON', u.id, u.public_id,
             u.first_name || ' ' || u.last_name,
             p.headline,
             p.bio,
             'profile.read',
             COALESCE(p.profile_visibility, u.default_visibility, 'PUBLIC')
        FROM public.users u
        LEFT JOIN public.user_profiles p ON p.user_id = u.id
       WHERE u.id = p_entity_id AND u.deleted_at IS NULL;

    -- Company-scoped entities are indexed with visibility = CONNECTIONS so the
    -- search RLS policy requires company context plus the listed permission.
    -- Public people/posts carry visibility = PUBLIC and no company_id.
    WHEN 'COMPANY' THEN
      INSERT INTO public.search_index
        (entity_type, entity_id, company_id, public_id, title, subtitle, body,
         required_permission, visibility)
      SELECT 'COMPANY', c.id, c.id, c.public_id, c.display_name, c.legal_name, NULL,
             'companies.read', 'CONNECTIONS'
        FROM public.companies c WHERE c.id = p_entity_id AND c.deleted_at IS NULL;

    WHEN 'PROJECT' THEN
      INSERT INTO public.search_index
        (entity_type, entity_id, company_id, public_id, title, subtitle, body,
         required_permission, visibility)
      SELECT 'PROJECT', pr.id, pr.company_id, pr.public_id, pr.name, pr.project_type,
             pr.description, 'projects.read', 'CONNECTIONS'
        FROM public.projects pr WHERE pr.id = p_entity_id AND pr.deleted_at IS NULL;

    WHEN 'SOW' THEN
      INSERT INTO public.search_index
        (entity_type, entity_id, company_id, public_id, title, subtitle, body,
         required_permission, visibility)
      SELECT 'SOW', s.id, s.company_id, s.public_id, s.title, s.sow_type, s.description,
             'sows.read', 'CONNECTIONS'
        FROM public.sows s WHERE s.id = p_entity_id AND s.deleted_at IS NULL;

    WHEN 'CONTRACT' THEN
      INSERT INTO public.search_index
        (entity_type, entity_id, company_id, public_id, title, subtitle, body,
         required_permission, visibility)
      SELECT 'CONTRACT', ct.id, ct.company_id, ct.public_id, ct.title, ct.contract_type,
             NULL, 'contracts.read', 'CONNECTIONS'
        FROM public.contracts ct WHERE ct.id = p_entity_id AND ct.deleted_at IS NULL;

    WHEN 'INVOICE' THEN
      INSERT INTO public.search_index
        (entity_type, entity_id, company_id, public_id, title, subtitle, body,
         required_permission, visibility)
      SELECT 'INVOICE', i.id, i.company_id, i.public_id, i.public_id,
             i.direction::text || ' ' || i.status::text,
             i.invoice_number, 'invoices.read', 'CONNECTIONS'
        FROM public.invoices i WHERE i.id = p_entity_id AND i.deleted_at IS NULL;

    WHEN 'DOCUMENT' THEN
      INSERT INTO public.search_index
        (entity_type, entity_id, company_id, public_id, title, subtitle, body,
         required_permission, visibility)
      SELECT 'DOCUMENT', d.id, d.company_id, d.public_id, d.title, d.doc_type,
             d.description, 'documents.read', 'CONNECTIONS'
        FROM public.documents d WHERE d.id = p_entity_id AND d.deleted_at IS NULL;

    WHEN 'POST' THEN
      INSERT INTO public.search_index
        (entity_type, entity_id, company_id, public_id, title, subtitle, body,
         required_permission, visibility)
      SELECT 'POST', po.id, po.company_id, po.public_id,
             left(po.content, 120),
             po.post_type,
             po.content,
             'posts.read',
             CASE WHEN po.company_id IS NULL THEN po.visibility ELSE 'CONNECTIONS' END
        FROM public.posts po WHERE po.id = p_entity_id AND po.deleted_at IS NULL;

    ELSE
      RAISE EXCEPTION 'unknown entity type %', p_entity_type USING ERRCODE = 'invalid_parameter_value';
  END CASE;
END
$$;

COMMENT ON FUNCTION app.reindex_entity(text, uuid) IS
  'Refreshes one search row. Called after every entity write (in the same transaction) by a database trigger.';

-- Keep the index in step with the domain tables automatically.
CREATE OR REPLACE FUNCTION app.search_autoreindex() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, app, pg_temp
AS $$
DECLARE v_id uuid := COALESCE(NEW.id, OLD.id);
BEGIN
  -- Migrations, seeds and background workers have no end-user identity.
  -- mytrakin_api is neither superuser nor BYPASSRLS, so request traffic
  -- always falls through to the real checks below.
  IF app.is_trusted_context() THEN
    RETURN NEW;
  END IF;

  -- Soft deletes simply deactivate the row.
  IF TG_OP = 'UPDATE' AND (NEW.deleted_at IS NOT NULL) AND (OLD.deleted_at IS NULL) THEN
    UPDATE public.search_index SET is_active = false
     WHERE entity_type = TG_ARGV[0]::text AND entity_id = v_id;
  ELSE
    PERFORM app.reindex_entity(TG_ARGV[0]::text, v_id);
  END IF;
  RETURN NULL;
END
$$;

DROP TRIGGER IF EXISTS trg_search_users ON public.users;
CREATE TRIGGER trg_search_users AFTER INSERT OR UPDATE ON public.users
  FOR EACH ROW EXECUTE FUNCTION app.search_autoreindex('PERSON');
DROP TRIGGER IF EXISTS trg_search_companies ON public.companies;
CREATE TRIGGER trg_search_companies AFTER INSERT OR UPDATE ON public.companies
  FOR EACH ROW EXECUTE FUNCTION app.search_autoreindex('COMPANY');
DROP TRIGGER IF EXISTS trg_search_projects ON public.projects;
CREATE TRIGGER trg_search_projects AFTER INSERT OR UPDATE ON public.projects
  FOR EACH ROW EXECUTE FUNCTION app.search_autoreindex('PROJECT');
DROP TRIGGER IF EXISTS trg_search_sows ON public.sows;
CREATE TRIGGER trg_search_sows AFTER INSERT OR UPDATE ON public.sows
  FOR EACH ROW EXECUTE FUNCTION app.search_autoreindex('SOW');
DROP TRIGGER IF EXISTS trg_search_contracts ON public.contracts;
CREATE TRIGGER trg_search_contracts AFTER INSERT OR UPDATE ON public.contracts
  FOR EACH ROW EXECUTE FUNCTION app.search_autoreindex('CONTRACT');
DROP TRIGGER IF EXISTS trg_search_invoices ON public.invoices;
CREATE TRIGGER trg_search_invoices AFTER INSERT OR UPDATE ON public.invoices
  FOR EACH ROW EXECUTE FUNCTION app.search_autoreindex('INVOICE');
DROP TRIGGER IF EXISTS trg_search_documents ON public.documents;
CREATE TRIGGER trg_search_documents AFTER INSERT OR UPDATE ON public.documents
  FOR EACH ROW EXECUTE FUNCTION app.search_autoreindex('DOCUMENT');
DROP TRIGGER IF EXISTS trg_search_posts ON public.posts;
CREATE TRIGGER trg_search_posts AFTER INSERT OR UPDATE ON public.posts
  FOR EACH ROW EXECUTE FUNCTION app.search_autoreindex('POST');

-- Profile edits change the PERSON projection.
CREATE OR REPLACE FUNCTION app.search_autoreindex_profile() RETURNS trigger
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

  PERFORM app.reindex_entity('PERSON', NEW.user_id);
  RETURN NULL;
END
$$;

DROP TRIGGER IF EXISTS trg_search_profiles ON public.user_profiles;
CREATE TRIGGER trg_search_profiles AFTER INSERT OR UPDATE ON public.user_profiles
  FOR EACH ROW EXECUTE FUNCTION app.search_autoreindex_profile();

-- -----------------------------------------------------------------------------
-- The search entry point.
-- Stable signature so the backing store can be swapped for OpenSearch later:
--   app.search_all(query, company_id, types[], limit) -> uniform rows.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.search_all(
  p_query text,
  p_company_id uuid DEFAULT NULL,
  p_entity_types text[] DEFAULT NULL,
  p_result_limit int DEFAULT 20
) RETURNS TABLE (
  entity_type text,
  public_id text,
  title text,
  subtitle text,
  company_id uuid,
  rank real
)
LANGUAGE sql STABLE
SET search_path = public, app, pg_temp
AS $$
  WITH q AS (
    SELECT
      CASE
        WHEN p_query ~ '^[a-zA-Z0-9 ]+$' AND length(p_query) >= 3
          THEN to_tsquery('english', p_query || ':*')
        ELSE NULL
      END AS tsq,
      lower(p_query) AS lq
  )
  SELECT si.entity_type, si.public_id, si.title, si.subtitle, si.company_id,
         CASE
           WHEN q.tsq IS NOT NULL AND si.entity_tsv @@ q.tsq
             THEN ts_rank_cd(si.entity_tsv, q.tsq)
           ELSE 0
         END
         + similarity(si.entity_trgm, q.lq) * 0.5
         + (si.popularity * 0.0001) AS rank
    FROM public.search_index si
    CROSS JOIN q
   WHERE si.is_active
     AND (p_entity_types IS NULL OR si.entity_type = ANY (p_entity_types))
     AND (
       -- Permission filter comes FIRST, before any ranking or content exposure.
       (si.visibility = 'PUBLIC' AND si.company_id IS NULL)
       OR (si.company_id IS NOT NULL
           AND (si.company_id = app.current_company_id() OR si.company_id = p_company_id)
           AND app.has_permission(si.company_id, si.required_permission))
     )
     AND (
       (q.tsq IS NOT NULL AND si.entity_tsv @@ q.tsq)
       OR si.entity_trgm ILIKE '%' || q.lq || '%'
     )
   ORDER BY rank DESC
   LIMIT LEAST(GREATEST(p_result_limit, 1), 100);
$$;

COMMENT ON FUNCTION app.search_all(text, uuid, text[], int) IS
  'Permission-aware global search. Swappable backend: keep this signature when introducing OpenSearch.';

-- -----------------------------------------------------------------------------
-- Company dashboard aggregates (materialised for the dashboard read path)
-- -----------------------------------------------------------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS public.company_dashboard_stats AS
SELECT
  c.id AS company_id,
  (SELECT count(*) FROM public.company_memberships m
    WHERE m.company_id = c.id AND m.status = 'ACTIVE')::int AS active_members,
  (SELECT count(*) FROM public.projects p
    WHERE p.company_id = c.id AND p.deleted_at IS NULL AND p.status = 'ACTIVE')::int AS active_projects,
  (SELECT count(*) FROM public.contracts ct
    WHERE ct.company_id = c.id AND ct.deleted_at IS NULL AND ct.status = 'ACTIVE')::int AS active_contracts,
  (SELECT COALESCE(sum(i.balance_due), 0)
     FROM public.invoices i
    WHERE i.company_id = c.id AND i.deleted_at IS NULL AND i.direction = 'RECEIVABLE'
      AND i.status IN ('SUBMITTED','APPROVED','PARTIALLY_PAID','OVERDUE'))::numeric(18,4) AS receivables,
  (SELECT COALESCE(sum(i.balance_due), 0)
     FROM public.invoices i
    WHERE i.company_id = c.id AND i.deleted_at IS NULL AND i.direction = 'PAYABLE'
      AND i.status IN ('SUBMITTED','APPROVED','PARTIALLY_PAID','OVERDUE'))::numeric(18,4) AS payables,
  (SELECT count(*) FROM public.invoices i
    WHERE i.company_id = c.id AND i.deleted_at IS NULL
      AND i.status IN ('SUBMITTED','APPROVED','PARTIALLY_PAID','OVERDUE')
      AND i.due_date < current_date)::int AS overdue_count,
  (SELECT count(*) FROM public.timesheets t
    WHERE t.company_id = c.id AND t.status IN ('SUBMITTED','UNDER_REVIEW'))::int AS pending_timesheets,
  (SELECT count(*) FROM public.msas m
    WHERE (m.company_a_id = c.id OR m.company_b_id = c.id)
      AND m.status IN ('MSA_REQUESTED','MSA_SUBMITTED','UNDER_REVIEW'))::int AS pending_msas,
  (SELECT count(*) FROM public.contracts ct
    WHERE ct.company_id = c.id AND ct.deleted_at IS NULL AND ct.status = 'ACTIVE'
      AND ct.end_date IS NOT NULL
      AND ct.end_date BETWEEN current_date AND current_date + 60)::int AS contracts_expiring_60d
  , now() AS computed_at
FROM public.companies c
WHERE c.deleted_at IS NULL
WITH NO DATA;

ALTER MATERIALIZED VIEW public.company_dashboard_stats OWNER TO postgres;

CREATE UNIQUE INDEX IF NOT EXISTS ux_dashboard_stats_company
  ON public.company_dashboard_stats (company_id);

-- Refresh helper, called by a scheduled worker (never from a request path).
CREATE OR REPLACE FUNCTION app.refresh_dashboard_stats() RETURNS void
LANGUAGE sql AS $$
  REFRESH MATERIALIZED VIEW CONCURRENTLY public.company_dashboard_stats;
$$;

GRANT EXECUTE ON FUNCTION app.reindex_entity(text, uuid) TO mytrakin_worker;
GRANT EXECUTE ON FUNCTION app.search_all(text, uuid, text[], int) TO mytrakin_api, mytrakin_worker;
GRANT EXECUTE ON FUNCTION app.refresh_dashboard_stats() TO mytrakin_worker;

REVOKE ALL ON public.search_index FROM mytrakin_api;
GRANT SELECT ON public.search_index TO mytrakin_api;
