-- =============================================================================
-- MyTrakin :: 0005_social.sql
-- Connections, feed, messaging, reports, blocking.
-- Privacy model: profile visibility is enforced in SQL before any read.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- user_blocks — checked before every connection/message/search action
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.user_blocks (
  id           uuid PRIMARY KEY DEFAULT app.uuid7(),
  blocker_id   uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  blocked_id   uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  reason       text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (blocker_id, blocked_id),
  CONSTRAINT ck_no_self_block CHECK (blocker_id <> blocked_id)
);

CREATE INDEX IF NOT EXISTS ix_blocks_blocked ON public.user_blocks (blocked_id);

-- Symmetric lookup helper used by feed/search/messaging.
-- p_b = NULL means "is p_a blocked by the caller, in either direction".
CREATE OR REPLACE FUNCTION app.is_blocked(
  p_a uuid,
  p_b uuid DEFAULT NULL,
  p_user_id uuid DEFAULT app.current_user_id()
) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, app, pg_temp
AS $$
  SELECT p_a IS NOT NULL
     AND EXISTS (
       SELECT 1 FROM public.user_blocks b
        WHERE (b.blocker_id = p_user_id AND b.blocked_id = p_a)
           OR (b.blocker_id = p_a     AND b.blocked_id = p_user_id)
           OR (p_b IS NOT NULL AND (
                 (b.blocker_id = p_user_id AND b.blocked_id = p_b)
              OR (b.blocker_id = p_b     AND b.blocked_id = p_user_id)))
     );
$$;

-- -----------------------------------------------------------------------------
-- connections — accepted relationships. Direction is normalised so that
-- (a,b) and (b,a) cannot both exist.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.connections (
  id           uuid PRIMARY KEY DEFAULT app.uuid7(),
  requester_id uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  addressee_id uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  -- normalised pair, guarantees one row per pair regardless of direction
  user_low     uuid GENERATED ALWAYS AS (LEAST(requester_id, addressee_id)) STORED,
  user_high    uuid GENERATED ALWAYS AS (GREATEST(requester_id, addressee_id)) STORED,
  status       text NOT NULL DEFAULT 'ACCEPTED'
                 CHECK (status IN ('PENDING','ACCEPTED','DECLINED','REMOVED')),
  mutual_count int NOT NULL DEFAULT 0,
  accepted_at  timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_low, user_high),
  CONSTRAINT ck_no_self_connection CHECK (requester_id <> addressee_id)
);

CREATE INDEX IF NOT EXISTS ix_conn_requester ON public.connections (requester_id, status);
CREATE INDEX IF NOT EXISTS ix_conn_addressee ON public.connections (addressee_id, status);
CREATE INDEX IF NOT EXISTS ix_conn_low  ON public.connections (user_low, status);
CREATE INDEX IF NOT EXISTS ix_conn_high ON public.connections (user_high, status);

COMMENT ON COLUMN public.connections.user_low IS
  'Normalised pair key. The UNIQUE constraint on (user_low, user_high) makes duplicate connections impossible.';

-- Enforce the normalised invariant on write.
CREATE OR REPLACE FUNCTION app.normalize_connection_pair() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.user_low = NEW.user_high THEN
    RAISE EXCEPTION 'self-connections are not allowed' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_connection_normalize ON public.connections;
CREATE TRIGGER trg_connection_normalize BEFORE INSERT OR UPDATE ON public.connections
  FOR EACH ROW EXECUTE FUNCTION app.normalize_connection_pair();

-- mutual_count drives "people you may know" ranking. Recomputed on change so
-- the value can never drift from the graph.
CREATE OR REPLACE FUNCTION app.refresh_mutual_count() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, app, pg_temp
AS $$
DECLARE
  v_self  uuid := COALESCE(NEW.user_low,  OLD.user_low);
  v_other uuid := COALESCE(NEW.user_high, OLD.user_high);
  n int;
BEGIN
  -- Users who are an accepted connection of BOTH endpoints.
  WITH pairs AS (
    SELECT c.user_low AS lo, c.user_high AS hi
      FROM public.connections c
     WHERE c.status = 'ACCEPTED'
       AND (c.user_low = v_self OR c.user_high = v_self)
  ),
  peers AS (SELECT CASE WHEN lo = v_self THEN hi ELSE lo END AS uid FROM pairs),
  peers_of_other AS (
    SELECT CASE WHEN c.user_low = v_other THEN c.user_high ELSE c.user_low END AS uid
      FROM public.connections c
     WHERE c.status = 'ACCEPTED'
       AND (c.user_low = v_other OR c.user_high = v_other)
  )
  SELECT count(*) INTO n FROM (SELECT uid FROM peers INTERSECT SELECT uid FROM peers_of_other) x;

  UPDATE public.connections SET mutual_count = n
   WHERE user_low = v_self AND user_high = v_other;
  RETURN NULL;
END
$$;

DROP TRIGGER IF EXISTS trg_connection_mutual ON public.connections;
CREATE TRIGGER trg_connection_mutual AFTER INSERT OR UPDATE OR DELETE ON public.connections
  FOR EACH ROW EXECUTE FUNCTION app.refresh_mutual_count();


-- -----------------------------------------------------------------------------
-- connection requests (invitations) — kept separate so both directions of a
-- pending invitation are represented and can carry a message.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.connection_requests (
  id           uuid PRIMARY KEY DEFAULT app.uuid7(),
  requester_id uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  addressee_id uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  message      text,
  status       text NOT NULL DEFAULT 'PENDING'
                 CHECK (status IN ('PENDING','ACCEPTED','DECLINED','CANCELLED')),
  responded_at timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (requester_id, addressee_id, status)
);

CREATE INDEX IF NOT EXISTS ix_connreq_addressee
  ON public.connection_requests (addressee_id, created_at DESC) WHERE status = 'PENDING';
CREATE INDEX IF NOT EXISTS ix_connreq_requester
  ON public.connection_requests (requester_id, created_at DESC);

-- -----------------------------------------------------------------------------
-- posts — professional feed content. Optional company/project/contract context.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.posts (
  id            uuid PRIMARY KEY DEFAULT app.uuid7(),
  public_id     text NOT NULL UNIQUE,
  author_id     uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  company_id    uuid REFERENCES public.companies(id) ON DELETE SET NULL,
  content       text NOT NULL CHECK (length(content) BETWEEN 1 AND 5000),
  content_tsv   tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
  post_type     text NOT NULL DEFAULT 'STANDARD'
                  CHECK (post_type IN ('STANDARD','ARTICLE','QUESTION','POLL','ANNOUNCEMENT')),
  visibility    public.visibility NOT NULL DEFAULT 'PUBLIC',
  company_id_required boolean NOT NULL DEFAULT false,   -- posts as the company voice
  document_id   uuid,                                   -- FK added in 0006 (documents)
  project_id    uuid,                                   -- FK added in 0006 (projects)
  reaction_count  int NOT NULL DEFAULT 0,
  comment_count   int NOT NULL DEFAULT 0,
  share_count     int NOT NULL DEFAULT 0,
  edited_at     timestamptz,
  deleted_at    timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_company_post_visibility
    CHECK (NOT company_id_required OR visibility = 'CONNECTIONS')
);

COMMENT ON COLUMN public.posts.company_id_required IS
  'Company-voice posts are visible to members only, never to the public network.';

CREATE INDEX IF NOT EXISTS ix_posts_author   ON public.posts (author_id, created_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_posts_company  ON public.posts (company_id, created_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_posts_feed     ON public.posts (created_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_posts_fts     ON public.posts USING gin (content_tsv);
CREATE INDEX IF NOT EXISTS ix_posts_company_required
  ON public.posts (company_id, created_at DESC)
  WHERE deleted_at IS NULL AND company_id_required;

CREATE OR REPLACE FUNCTION app.assign_post_public_id() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_try int := 0; v_id text;
BEGIN

  LOOP
    v_id := app.gen_public_id('PO', 8);
    EXIT WHEN NOT EXISTS (SELECT 1 FROM public.posts WHERE public_id = v_id);
    v_try := v_try + 1;
    IF v_try > 5 THEN RAISE EXCEPTION 'could not allocate post public_id'; END IF;
  END LOOP;
  NEW.public_id := v_id;
  RETURN NEW;
END
$$;

SELECT app.attach_triggers('public.posts'::regclass);
DROP TRIGGER IF EXISTS trg_posts_public_id ON public.posts;
CREATE TRIGGER trg_posts_public_id BEFORE INSERT ON public.posts
  FOR EACH ROW WHEN (NEW.public_id IS NULL)
  EXECUTE FUNCTION app.assign_post_public_id();

-- -----------------------------------------------------------------------------
-- post_comments
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.post_comments (
  id          uuid PRIMARY KEY DEFAULT app.uuid7(),
  post_id     uuid NOT NULL REFERENCES public.posts(id) ON DELETE CASCADE,
  author_id   uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  parent_id   uuid REFERENCES public.post_comments(id) ON DELETE CASCADE,
  content     text NOT NULL CHECK (length(content) BETWEEN 1 AND 2000),
  deleted_at  timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_comments_post ON public.post_comments (post_id, created_at) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_comments_author ON public.post_comments (author_id, created_at DESC);

-- -----------------------------------------------------------------------------
-- post_reactions
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.post_reactions (
  post_id     uuid NOT NULL REFERENCES public.posts(id) ON DELETE CASCADE,
  user_id     uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  reaction    text NOT NULL DEFAULT 'LIKE'
                CHECK (reaction IN ('LIKE','CELEBRATE','SUPPORT','INSIGHTFUL','CURIOUS')),
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (post_id, user_id, reaction)
);

CREATE INDEX IF NOT EXISTS ix_reactions_user ON public.post_reactions (user_id, created_at DESC);

-- -----------------------------------------------------------------------------
-- post_shares — a share is a distinct post to preserve attribution.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.post_shares (
  id            uuid PRIMARY KEY DEFAULT app.uuid7(),
  post_id       uuid NOT NULL REFERENCES public.posts(id) ON DELETE CASCADE,
  user_id       uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  shared_post_id uuid REFERENCES public.posts(id) ON DELETE CASCADE,
  commentary    text,
  visibility    public.visibility NOT NULL DEFAULT 'PUBLIC',
  company_id    uuid REFERENCES public.companies(id) ON DELETE SET NULL,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_shares_post ON public.post_shares (post_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_shares_user ON public.post_shares (user_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS ux_share_once
  ON public.post_shares (post_id, user_id) WHERE shared_post_id IS NULL;
-- Posting as the company voice requires an active membership that can post.
CREATE OR REPLACE FUNCTION app.assert_post_author() RETURNS trigger
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
  IF app.current_user_id() IS NOT NULL AND NEW.author_id <> app.current_user_id() THEN
    RAISE EXCEPTION 'posts may only be authored by the current user'
      USING ERRCODE = 'insufficient_privilege';
  END IF;

  IF NEW.company_id_required AND NEW.company_id IS NULL THEN
    RAISE EXCEPTION 'company post requires company_id' USING ERRCODE = 'check_violation';
  END IF;

  IF NEW.company_id IS NOT NULL
     AND NOT app.has_permission(NEW.company_id, 'posts.create') THEN
    RAISE EXCEPTION 'missing permission posts.create for company %', NEW.company_id
      USING ERRCODE = 'insufficient_privilege';
  END IF;

  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_posts_author_guard ON public.posts;
CREATE TRIGGER trg_posts_author_guard BEFORE INSERT OR UPDATE ON public.posts
  FOR EACH ROW EXECUTE FUNCTION app.assert_post_author();

-- Denormalised counters must stay truthful.
CREATE OR REPLACE FUNCTION app.refresh_post_counters() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, app, pg_temp
AS $$
DECLARE v_post uuid := COALESCE(NEW.post_id, OLD.post_id);
BEGIN
  UPDATE public.posts p SET
    reaction_count = (SELECT count(*) FROM public.post_reactions r
                       WHERE r.post_id = v_post AND p.id = v_post),
    comment_count  = (SELECT count(*) FROM public.post_comments c
                       WHERE c.post_id = v_post AND c.deleted_at IS NULL AND p.id = v_post),
    share_count    = (SELECT count(*) FROM public.post_shares s
                       WHERE s.post_id = v_post AND p.id = v_post)
   WHERE p.id = v_post;
  RETURN NULL;
END
$$;

DROP TRIGGER IF EXISTS trg_reaction_counter ON public.post_reactions;
CREATE TRIGGER trg_reaction_counter AFTER INSERT OR DELETE ON public.post_reactions
  FOR EACH ROW EXECUTE FUNCTION app.refresh_post_counters();
DROP TRIGGER IF EXISTS trg_comment_counter ON public.post_comments;
CREATE TRIGGER trg_comment_counter AFTER INSERT OR UPDATE OR DELETE ON public.post_comments
  FOR EACH ROW EXECUTE FUNCTION app.refresh_post_counters();
DROP TRIGGER IF EXISTS trg_share_counter ON public.post_shares;
CREATE TRIGGER trg_share_counter AFTER INSERT OR DELETE ON public.post_shares
  FOR EACH ROW EXECUTE FUNCTION app.refresh_post_counters();

-- -----------------------------------------------------------------------------
-- Feed visibility predicate.
-- A post is visible when: author is self, OR author is an accepted connection,
-- OR the post is public, OR the caller is an active member of the post's company.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.can_view_post(
  p_post_id uuid,
  p_user_id uuid DEFAULT app.current_user_id()
) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, app, pg_temp
AS $$
  SELECT EXISTS (
    SELECT 1
      FROM public.posts p
     WHERE p.id = p_post_id
       AND p.deleted_at IS NULL
       AND NOT app.is_blocked(p.author_id, NULL, p_user_id)
       AND (
         p.author_id = p_user_id
         OR EXISTS (
              SELECT 1 FROM public.connections c
               WHERE c.status = 'ACCEPTED'
                 AND c.user_low  = LEAST(p_user_id, p.author_id)
                 AND c.user_high = GREATEST(p_user_id, p.author_id)
                 AND (c.requester_id = p_user_id OR c.addressee_id = p_user_id)
         )
         OR p.visibility = 'PUBLIC'
         OR (p.company_id IS NOT NULL AND app.is_member(p.company_id, p_user_id))
       )
  );
$$;

COMMENT ON FUNCTION app.can_view_post(uuid, uuid) IS
  'Feed visibility. Company-voice posts are additionally constrained to company members via company_id_required.';

-- -----------------------------------------------------------------------------
-- Messaging: conversations, participants, messages.
-- Group messaging is supported structurally (conversation_members) while the
-- 1:1 rule is enforced by app.has_any_permission(participants >= 2) and API checks.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.conversations (
  id           uuid PRIMARY KEY DEFAULT app.uuid7(),
  public_id    text NOT NULL UNIQUE,
  kind         text NOT NULL DEFAULT 'DIRECT' CHECK (kind IN ('DIRECT','GROUP','SYSTEM')),
  company_id   uuid REFERENCES public.companies(id) ON DELETE CASCADE,
  title        text,
  created_by   uuid REFERENCES public.users(id) ON DELETE SET NULL,
  last_message_at timestamptz,
  message_count   int NOT NULL DEFAULT 0,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  deleted_at   timestamptz
);

CREATE OR REPLACE FUNCTION app.assign_conversation_public_id() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_try int := 0; v_id text;
BEGIN

  LOOP
    v_id := app.gen_public_id('CV', 8);
    EXIT WHEN NOT EXISTS (SELECT 1 FROM public.conversations WHERE public_id = v_id);
    v_try := v_try + 1;
    IF v_try > 5 THEN RAISE EXCEPTION 'could not allocate conversation public_id'; END IF;
  END LOOP;
  NEW.public_id := v_id;
  RETURN NEW;
END
$$;

SELECT app.attach_triggers('public.conversations'::regclass);
DROP TRIGGER IF EXISTS trg_conversations_public_id ON public.conversations;
CREATE TRIGGER trg_conversations_public_id BEFORE INSERT ON public.conversations
  FOR EACH ROW WHEN (NEW.public_id IS NULL)
  EXECUTE FUNCTION app.assign_conversation_public_id();

CREATE TABLE IF NOT EXISTS public.conversation_members (
  conversation_id uuid NOT NULL REFERENCES public.conversations(id) ON DELETE CASCADE,
  user_id         uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  role            text NOT NULL DEFAULT 'MEMBER' CHECK (role IN ('OWNER','MEMBER')),
  last_read_at    timestamptz,
  muted_until     timestamptz,
  joined_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (conversation_id, user_id)
);

CREATE INDEX IF NOT EXISTS ix_conv_members_user
  ON public.conversation_members (user_id, conversation_id);
CREATE INDEX IF NOT EXISTS ix_conv_members_unread
  ON public.conversation_members (user_id) WHERE last_read_at IS NULL;

CREATE TABLE IF NOT EXISTS public.messages (
  id              uuid PRIMARY KEY DEFAULT app.uuid7(),
  conversation_id uuid NOT NULL REFERENCES public.conversations(id) ON DELETE CASCADE,
  sender_id       uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  content         text CHECK (content IS NOT NULL OR document_id IS NOT NULL),
  document_id     uuid,                      -- FK added in 0006 (documents)
  reply_to_id     uuid REFERENCES public.messages(id) ON DELETE SET NULL,
  content_tsv     tsvector GENERATED ALWAYS AS (to_tsvector('english', coalesce(content, ''))) STORED,
  edited_at       timestamptz,
  deleted_at      timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_message_has_content CHECK (content IS NOT NULL OR document_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS ix_messages_conversation
  ON public.messages (conversation_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_messages_sender ON public.messages (sender_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_messages_fts  ON public.messages USING gin (content_tsv);

-- Messaging requires an accepted connection (Phase 2 rule enforced server-side).
CREATE OR REPLACE FUNCTION app.can_message(
  p_a uuid,
  p_b uuid,
  p_user_id uuid DEFAULT app.current_user_id()
) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, app, pg_temp
AS $$
  SELECT p_a <> p_b
     AND NOT app.is_blocked(p_a, p_b, p_user_id)
     AND EXISTS (
       SELECT 1 FROM public.connections c
        WHERE c.status = 'ACCEPTED'
          AND c.user_low  = LEAST(p_a, p_b)
          AND c.user_high = GREATEST(p_a, p_b)
     );
$$;

-- -----------------------------------------------------------------------------
-- reports — user/content moderation
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.reports (
  id             uuid PRIMARY KEY DEFAULT app.uuid7(),
  reporter_id    uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  target_type    text NOT NULL CHECK (target_type IN ('USER','POST','COMMENT','MESSAGE','COMPANY','DOCUMENT')),
  target_id      uuid NOT NULL,
  reason         text NOT NULL,
  details        text,
  status         text NOT NULL DEFAULT 'OPEN'
                   CHECK (status IN ('OPEN','REVIEWING','RESOLVED','DISMISSED')),
  resolution     text,
  resolved_by    uuid REFERENCES public.users(id) ON DELETE SET NULL,
  resolved_at    timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_reports_open ON public.reports (status, created_at DESC) WHERE status IN ('OPEN','REVIEWING');
CREATE INDEX IF NOT EXISTS ix_reports_target ON public.reports (target_type, target_id);

CREATE OR REPLACE FUNCTION app.touch_updated_at_public() RETURNS trigger
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

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['post_comments','post_shares','connection_requests','reports'] LOOP
    PERFORM app.attach_updated_at(
      format('public.%I', t)::regclass,
      'trg_' || t || '_updated_at');
  END LOOP;
END
$$;

GRANT EXECUTE ON FUNCTION app.can_view_post(uuid, uuid) TO mytrakin_api, mytrakin_worker;
GRANT EXECUTE ON FUNCTION app.can_message(uuid, uuid, uuid) TO mytrakin_api, mytrakin_worker;
GRANT EXECUTE ON FUNCTION app.is_blocked(uuid, uuid, uuid) TO mytrakin_api, mytrakin_worker;

GRANT SELECT, INSERT, UPDATE, DELETE ON
  public.user_blocks, public.connections, public.connection_requests,
  public.posts, public.post_comments, public.post_reactions, public.post_shares,
  public.conversations, public.conversation_members, public.messages, public.reports
TO mytrakin_api, mytrakin_worker;
