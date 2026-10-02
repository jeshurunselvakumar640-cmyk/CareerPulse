-- CareerPulse — Application Communication / Reply Tracking
--
-- Run this once in the Supabase SQL editor (or with psql against the project
-- database). It is additive: existing tables, rows, and the SMTP send path are
-- untouched. The application degrades safely if it has not been applied yet.
--
-- 1. email_logs is REUSED as the sent-email table. It already stores
--    user_id, opportunity_id, recipient_email, subject, body, status, sent_at.
--    These columns add the threading identity needed to match a reply:
--      message_id           the RFC 5322 Message-ID CareerPulse generated
--      sender_email         the mailbox the outreach was sent from
--      thread_id            provider thread id, learned from the first reply
--      provider             'gmail' (or another connector later)
--      provider_message_id  the provider's own id for the sent message
--      company / title      display fields for the Applications list
-- 2. mailbox_connections holds the encrypted OAuth tokens, one row per user.
-- 3. email_replies holds replies linked to an application.

-- -------------------------------------------------------------------
-- 1. Extend the existing sent-email table
-- -------------------------------------------------------------------
alter table public.email_logs add column if not exists message_id text;
alter table public.email_logs add column if not exists sender_email text;
alter table public.email_logs add column if not exists thread_id text;
alter table public.email_logs add column if not exists provider text;
alter table public.email_logs add column if not exists provider_message_id text;
alter table public.email_logs add column if not exists company text;
alter table public.email_logs add column if not exists title text;
alter table public.email_logs add column if not exists updated_at timestamptz default now();

create index if not exists email_logs_user_id_idx
    on public.email_logs (user_id);
create index if not exists email_logs_message_id_idx
    on public.email_logs (message_id);
create index if not exists email_logs_provider_message_id_idx
    on public.email_logs (provider, provider_message_id);
create index if not exists email_logs_thread_id_idx
    on public.email_logs (thread_id);

-- -------------------------------------------------------------------
-- 2. Mailbox connection (OAuth tokens are stored ENCRYPTED, never plain)
-- -------------------------------------------------------------------
create table if not exists public.mailbox_connections (
    id                      uuid primary key default gen_random_uuid(),
    user_id                 uuid not null unique,
    provider                text not null default 'gmail',
    mailbox_email           text,
    access_token_encrypted  text,
    refresh_token_encrypted text,
    token_expires_at        timestamptz,
    scopes                  text,
    status                  text not null default 'connected',
    last_sync_at            timestamptz,
    last_sync_error         text,
    created_at              timestamptz not null default now(),
    updated_at              timestamptz not null default now()
);

create index if not exists mailbox_connections_user_id_idx
    on public.mailbox_connections (user_id);

-- -------------------------------------------------------------------
-- 3. Received replies
-- -------------------------------------------------------------------
create table if not exists public.email_replies (
    id                       uuid primary key default gen_random_uuid(),
    user_id                  uuid not null,
    opportunity_id           text not null,
    sent_email_id            uuid,
    thread_id                text,
    provider                 text not null default 'gmail',
    provider_message_id      text not null,
    internet_message_id      text,
    sender_email             text not null,
    sender_name              text,
    recipient_email          text,
    subject                  text,
    body_text                text,
    received_at              timestamptz,
    is_read                  boolean not null default false,
    has_attachments          boolean not null default false,
    attachment_names         text[] default '{}',
    classification           text,
    classification_confidence numeric,
    classification_summary   text,
    requires_action          boolean not null default false,
    requested_information    text[] default '{}',
    suggested_status         text,
    match_method             text,
    created_at               timestamptz not null default now(),
    updated_at               timestamptz not null default now(),

    -- Duplicate prevention: the same provider message can never be stored twice.
    constraint email_replies_provider_message_uniq
        unique (provider, provider_message_id)
);

create index if not exists email_replies_user_id_idx
    on public.email_replies (user_id);
create index if not exists email_replies_opportunity_idx
    on public.email_replies (user_id, opportunity_id);
create index if not exists email_replies_thread_idx
    on public.email_replies (thread_id);
create index if not exists email_replies_unread_idx
    on public.email_replies (user_id, is_read);

-- Keep updated_at honest without relying on the application layer.
create or replace function public.set_updated_at()
returns trigger as $$
begin
    new.updated_at = now();
    return new;
end;
$$ language plpgsql;

drop trigger if exists email_replies_set_updated_at on public.email_replies;
create trigger email_replies_set_updated_at
    before update on public.email_replies
    for each row execute function public.set_updated_at();

drop trigger if exists mailbox_connections_set_updated_at on public.mailbox_connections;
create trigger mailbox_connections_set_updated_at
    before update on public.mailbox_connections
    for each row execute function public.set_updated_at();

-- -------------------------------------------------------------------
-- Security note (optional, recommended for production)
-- -------------------------------------------------------------------
-- The API is the only writer and always filters by the authenticated user id.
-- If Row Level Security is enabled for this project, also add:
--
--   alter table public.email_replies enable row level security;
--   alter table public.mailbox_connections enable row level security;
--   create policy "own replies" on public.email_replies
--       for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
--   create policy "own mailbox" on public.mailbox_connections
--       for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
--
-- Leave RLS as-is until then so the current service-role key keeps working.

-- -------------------------------------------------------------------
-- Verification
-- -------------------------------------------------------------------
-- select column_name from information_schema.columns
--   where table_name = 'email_logs' and column_name = 'message_id';
