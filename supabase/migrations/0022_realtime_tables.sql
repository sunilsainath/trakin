-- =============================================================================
-- MyTrakin :: 0021_realtime_tables.sql
--
-- Publishes the two user-scoped streams the UI subscribes to:
--   platform.notifications  badge counts for the signed-in user
--   public.messages         new chat messages in open threads
--
-- Safety: both tables carry SELECT policies for the PUBLIC role scoped to the
-- caller's own rows (session identity), which is exactly what Supabase
-- Realtime enforces per subscriber. No company-wide table is published.
-- Replica identity defaults to the primary key, which is all INSERT
-- subscribers need.
-- =============================================================================

ALTER PUBLICATION supabase_realtime ADD TABLE platform.notifications;
ALTER PUBLICATION supabase_realtime ADD TABLE public.messages;
