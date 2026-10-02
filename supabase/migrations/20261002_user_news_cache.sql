-- CareerPulse — Personalized Tech News Cache
--
-- Run this once in the Supabase SQL editor (or with psql against the project
-- database). It is additive: no existing table, row, or code path is modified.
-- The application degrades safely if it has not been applied yet — the news
-- pipeline falls back to the in-process cache (personalized_news._MEMORY_CACHE).
--
-- The table name and shape are dictated by app/services/personalized_news.py:
--   _SUPABASE_CACHE_TABLE = "user_news_cache"      (personalized_news.py:772)
--   .upsert(payload, on_conflict="user_id")        (personalized_news.py:818)
--   .select("*").eq("user_id", user_id)            (personalized_news.py:787)
--
-- Column types are chosen to match exactly what that code writes and reads:
--
--   user_id             uuid    written as user.id (a Supabase auth UUID).
--                               Read back only as a dict key, so uuid is safe
--                               and matches the existing convention in
--                               20260101_reply_tracking.sql (mailbox_connections
--                               and email_replies both use `user_id uuid`).
--                               Primary key, so on_conflict="user_id" upserts
--                               on the identity column.
--
--   profile_fingerprint text    sha256 hex string; compared with `==` against
--                               the freshly computed fingerprint.
--
--   fetched_at          double precision
--                               NOT timestamptz. The code stores time.time() and
--                               reads it back with float(row["fetched_at"])
--                               (personalized_news.py:796, and the TTL checks at
--                               :995-996). A timestamptz column is returned by
--                               PostgREST as an ISO-8601 string, which float()
--                               cannot parse; that would raise ValueError on every
--                               read and permanently disable the cache. A numeric
--                               column round-trips a Python float exactly.
--
--   articles            jsonb    list of shaped article dicts. PostgREST returns
--                               jsonb already decoded, so the isinstance(str)
--                               branch at personalized_news.py:791 is not taken.
--
-- Row Level Security is ENABLED and is not disabled here. Each policy is
-- restricted to `authenticated` and scoped to the caller's own row, so one
-- account can never read or overwrite another account's cached feed.
--
-- NOTE: The backend uses the server-side service-role Supabase client for this
-- cache; SUPABASE_SERVICE_ROLE_KEY must be configured for persistent cache storage.

create table if not exists public.user_news_cache (
    user_id             uuid primary key,
    profile_fingerprint text not null default '',
    fetched_at          double precision not null default extract(epoch from now()),
    articles            jsonb not null default '[]'::jsonb
);

create index if not exists user_news_cache_fetched_at_idx
    on public.user_news_cache (fetched_at);

-- -------------------------------------------------------------------
-- Row Level Security: one row per user, reachable only by that user
-- -------------------------------------------------------------------
alter table public.user_news_cache enable row level security;

drop policy if exists "own news cache select" on public.user_news_cache;
create policy "own news cache select"
    on public.user_news_cache
    for select
    to authenticated
    using (auth.uid() = user_id);

drop policy if exists "own news cache insert" on public.user_news_cache;
create policy "own news cache insert"
    on public.user_news_cache
    for insert
    to authenticated
    with check (auth.uid() = user_id);

drop policy if exists "own news cache update" on public.user_news_cache;
create policy "own news cache update"
    on public.user_news_cache
    for update
    to authenticated
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

-- -------------------------------------------------------------------
-- Verification
-- -------------------------------------------------------------------
-- select column_name, data_type
--   from information_schema.columns
--  where table_name = 'user_news_cache'
--   order by ordinal_position;
--
-- select relrowsecurity from pg_class where relname = 'user_news_cache';
--   -- expect: t
--
-- select polname, cmd, qual, with_check from pg_policies
--  where tablename = 'user_news_cache';
