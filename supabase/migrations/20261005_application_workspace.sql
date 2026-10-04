-- ===================================================================
-- Application Workspace
--
-- Additive only. Nothing here alters or drops an existing column, table,
-- constraint or behaviour. Existing tables keep their exact shape, and
-- every new object is created with `if not exists` so this file is safe to
-- re-apply.
--
-- What already exists and is deliberately NOT duplicated here:
--   * email_logs            - outbound outreach mail sent from CareerPulse
--   * email_replies         - inbound company replies (already deduped by
--                             unique (provider, provider_message_id))
--   * mailbox_connections   - the OAuth connection (Gmail + calendar grant)
--
-- This migration only adds what the application workspace needs on top:
--   1. Grounded next-step + interview extraction on a single reply.
--   2. A timeline of events that cannot be derived from the two tables
--      above (next steps extracted, interview scheduled, calendar event
--      created, reminder surfaced).
--   3. An interview entity, which is the thing a calendar event attaches to.
--
-- RLS is intentionally left off, matching mailbox_connections and
-- email_replies in 20260101_reply_tracking.sql: access is enforced
-- server-side by filtering every query on user_id, and the service-role key
-- is what the API uses.
-- ===================================================================

-- -------------------------------------------------------------------
-- 1. Reply analysis: grounded next steps + interview extraction.
--
-- Every column is nullable so an un-analysed reply is indistinguishable
-- from a pre-migration row, and so a Gemini failure can never corrupt a
-- reply that was already stored by the existing pipeline.
-- -------------------------------------------------------------------
alter table public.email_replies
    add column if not exists next_steps                 text[],
    add column if not exists analysis_state              text,
    add column if not exists analysis_error              text,
    add column if not exists analyzed_at                 timestamptz,
    add column if not exists interview_at                timestamptz,
    add column if not exists interview_timezone           text,
    add column if not exists interview_type              text,
    add column if not exists meeting_link                text,
    add column if not exists interview_location          text;

-- The user's timezone decision is recorded per interview. CareerPulse never
-- assumes a zone: when the email carries its own offset this stays false and
-- the email's own offset is used; when it does not, the interview is held back
-- for explicit confirmation instead of being guessed at.
alter table public.email_replies
    add column if not exists timezone_confirmed          boolean not null default false;

create index if not exists email_replies_analysis_state_idx
    on public.email_replies (user_id, analysis_state);

-- -------------------------------------------------------------------
-- 2. Application timeline.
--
-- Only events that CANNOT be derived from email_logs / email_replies are
-- stored here, which is what keeps PART 16 (duplicate protection) cheap:
-- applying the same Gmail message twice re-derives the same email rows and
-- lands on the same dedupe_key below.
-- -------------------------------------------------------------------
create table if not exists public.application_timeline_events (
    id               uuid primary key default gen_random_uuid(),
    user_id          uuid not null,
    opportunity_id   text not null,
    event_type       text not null,
    title            text,
    detail           text,
    source_reply_id  uuid,
    occurred_at      timestamptz,
    -- Stable, deterministic identity for "this event for this input".
    dedupe_key       text not null,
    created_at       timestamptz not null default now()
);

-- The authoritative duplicate guard. Processing one Gmail message twice can
-- never produce two timeline events.
create unique index if not exists application_timeline_events_dedupe_idx
    on public.application_timeline_events (user_id, dedupe_key);

create index if not exists application_timeline_events_opportunity_idx
    on public.application_timeline_events (user_id, opportunity_id, occurred_at);

-- -------------------------------------------------------------------
-- 3. Interviews.
--
-- One row per reply that produced a definite, explicitly-stated date/time.
-- A reply that only says "we'd like to interview you soon" creates no row at
-- all, so an unscheduled interview can never reach a calendar.
-- -------------------------------------------------------------------
create table if not exists public.application_interviews (
    id                    uuid primary key default gen_random_uuid(),
    user_id               uuid not null,
    opportunity_id        text not null,
    source_reply_id       uuid,
    starts_at             timestamptz not null,
    timezone              text,
    timezone_confirmed    boolean not null default false,
    interview_type        text,
    meeting_link          text,
    location              text,
    notes                 text,
    -- Populated only after a successful calendar call. Nullable so a user
    -- without the calendar grant is never treated as broken.
    calendar_event_id     text,
    calendar_status       text not null default 'not_created',
    calendar_error        text,
    reminder_status       text not null default 'not_set',
    dedupe_key            text not null,
    created_at            timestamptz not null default now(),
    updated_at            timestamptz not null default now()
);

-- One interview per reply that states one. The extra provider/interview key
-- keeps a single reply naming several interviews from colliding.
create unique index if not exists application_interviews_dedupe_idx
    on public.application_interviews (user_id, dedupe_key);

create index if not exists application_interviews_opportunity_idx
    on public.application_interviews (user_id, opportunity_id, starts_at);

-- Reuse the trigger helper from 20260101_reply_tracking.sql rather than
-- defining a second copy of the same function.
drop trigger if exists application_interviews_set_updated_at on public.application_interviews;
create trigger application_interviews_set_updated_at
    before update on public.application_interviews
    for each row execute function public.set_updated_at();
