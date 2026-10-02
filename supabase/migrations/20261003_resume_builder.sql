-- CareerPulse — Resume Builder
--
-- Run this once in the Supabase SQL editor (or with psql against the project
-- database). It is additive: no existing table, row, or code path is modified.
--
-- OWNERSHIP MODEL
-- Every table here is user-scoped by `auth.uid()`. The browser never sends a
-- user id; app/main.py derives it from the validated Supabase session
-- (require_auth_token_and_user) and performs all reads/writes through a
-- client carrying that user's JWT, so these policies are the real enforcement
-- boundary rather than decoration. A user can only ever reach their own row.
--
-- Repeated sections (education, experience, references, projects,
-- publications) and the simple item lists (skills, hobbies, awards,
-- activities, languages) are separate child tables rather than one JSON blob,
-- so a single entry can be edited or deleted without rewriting the document.
--
-- Image binaries are NOT stored here. profile_picture_url and signature_url
-- hold storage paths under the `resume-assets` bucket; the bucket is private
-- and the backend hands the browser short-lived signed URLs.

-- ===================================================================
-- Parent document
-- ===================================================================
create table if not exists public.resume_profiles (
    id                        uuid primary key default gen_random_uuid(),
    user_id                   uuid not null references auth.users(id) on delete cascade,

    profile_picture_url       text,
    name                      text,
    email                     text,
    date_of_birth             date,
    gender                    text,
    linkedin_url              text,
    github_url                text,
    website_url               text,
    address                   text,
    pincode                   text,
    city                      text,
    state                     text,
    country                   text,

    headline                  text,
    summary                   text,
    additional_information    text,
    signature_url             text,

    -- Cached result of the experience calculation, in whole months.
    -- Recomputed by app/services/resume_service.py on every experience
    -- create/update/delete so the dashboard never has to recalculate.
    total_experience_months   integer not null default 0,

    created_at                timestamptz not null default now(),
    updated_at                timestamptz not null default now(),

    -- One resume per CareerPulse account. This is what makes the upsert in
    -- the backend deterministic, and it is the single-row anchor every child
    -- policy resolves through.
    constraint resume_profiles_user_unique unique (user_id)
);

create index if not exists resume_profiles_user_id_idx
    on public.resume_profiles (user_id);

-- ===================================================================
-- Child tables
-- ===================================================================
create table if not exists public.resume_education (
    id                  uuid primary key default gen_random_uuid(),
    resume_id           uuid not null references public.resume_profiles(id) on delete cascade,
    course_degree       text,
    school_university   text,
    grade_score         text,
    currently_doing     boolean not null default false,
    start_date          date,
    end_date            date,
    sort_order          integer not null default 0,
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now()
);

create index if not exists resume_education_resume_id_idx
    on public.resume_education (resume_id);

create table if not exists public.resume_experience (
    id                    uuid primary key default gen_random_uuid(),
    resume_id             uuid not null references public.resume_profiles(id) on delete cascade,
    company_name          text,
    job_title             text,
    currently_work_here   boolean not null default false,
    -- Constrained to the three values the UI offers.
    employment_type       text check (employment_type in ('Internship', 'Part Time Job', 'Full Time Job')),
    start_date            date,
    end_date              date,
    details               text,
    sort_order            integer not null default 0,
    created_at            timestamptz not null default now(),
    updated_at            timestamptz not null default now()
);

create index if not exists resume_experience_resume_id_idx
    on public.resume_experience (resume_id);

create table if not exists public.resume_references (
    id              uuid primary key default gen_random_uuid(),
    resume_id       uuid not null references public.resume_profiles(id) on delete cascade,
    referee_name    text,
    job_title       text,
    company_name    text,
    email           text,
    phone           text,
    sort_order      integer not null default 0,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

create index if not exists resume_references_resume_id_idx
    on public.resume_references (resume_id);

create table if not exists public.resume_projects (
    id          uuid primary key default gen_random_uuid(),
    resume_id   uuid not null references public.resume_profiles(id) on delete cascade,
    title       text,
    link        text,
    details     text,
    sort_order  integer not null default 0,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

create index if not exists resume_projects_resume_id_idx
    on public.resume_projects (resume_id);

create table if not exists public.resume_publications (
    id          uuid primary key default gen_random_uuid(),
    resume_id   uuid not null references public.resume_profiles(id) on delete cascade,
    title       text,
    link        text,
    details     text,
    sort_order  integer not null default 0,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

create index if not exists resume_publications_resume_id_idx
    on public.resume_publications (resume_id);

-- Simple single-column item lists (skills, hobbies, awards, activities,
-- languages). `unique (resume_id, value)` backs the duplicate guard in the UI
-- and makes a retry safe.
create table if not exists public.resume_skills (
    id          uuid primary key default gen_random_uuid(),
    resume_id   uuid not null references public.resume_profiles(id) on delete cascade,
    value       text not null,
    sort_order  integer not null default 0,
    created_at  timestamptz not null default now(),
    constraint resume_skills_unique unique (resume_id, value)
);

create index if not exists resume_skills_resume_id_idx
    on public.resume_skills (resume_id);

create table if not exists public.resume_hobbies (
    id          uuid primary key default gen_random_uuid(),
    resume_id   uuid not null references public.resume_profiles(id) on delete cascade,
    value       text not null,
    sort_order  integer not null default 0,
    created_at  timestamptz not null default now(),
    constraint resume_hobbies_unique unique (resume_id, value)
);

create index if not exists resume_hobbies_resume_id_idx
    on public.resume_hobbies (resume_id);

create table if not exists public.resume_awards (
    id          uuid primary key default gen_random_uuid(),
    resume_id   uuid not null references public.resume_profiles(id) on delete cascade,
    value       text not null,
    sort_order  integer not null default 0,
    created_at  timestamptz not null default now(),
    constraint resume_awards_unique unique (resume_id, value)
);

create index if not exists resume_awards_resume_id_idx
    on public.resume_awards (resume_id);

create table if not exists public.resume_activities (
    id          uuid primary key default gen_random_uuid(),
    resume_id   uuid not null references public.resume_profiles(id) on delete cascade,
    value       text not null,
    sort_order  integer not null default 0,
    created_at  timestamptz not null default now(),
    constraint resume_activities_unique unique (resume_id, value)
);

create index if not exists resume_activities_resume_id_idx
    on public.resume_activities (resume_id);

create table if not exists public.resume_languages (
    id          uuid primary key default gen_random_uuid(),
    resume_id   uuid not null references public.resume_profiles(id) on delete cascade,
    value       text not null,
    sort_order  integer not null default 0,
    created_at  timestamptz not null default now(),
    constraint resume_languages_unique unique (resume_id, value)
);

create index if not exists resume_languages_resume_id_idx
    on public.resume_languages (resume_id);

-- ===================================================================
-- Row Level Security
-- ===================================================================
-- ENABLED, never disabled. Every policy is `to authenticated` and scoped to
-- the caller's own row, so one account can never read, write or delete
-- another account's resume. Child tables resolve ownership by joining back to
-- the parent's user_id, which is the only row the caller is ever allowed to see.
-- ===================================================================

alter table public.resume_profiles  enable row level security;
alter table public.resume_education enable row level security;
alter table public.resume_experience enable row level security;
alter table public.resume_references enable row level security;
alter table public.resume_projects enable row level security;
alter table public.resume_publications enable row level security;
alter table public.resume_skills enable row level security;
alter table public.resume_hobbies enable row level security;
alter table public.resume_awards enable row level security;
alter table public.resume_activities enable row level security;
alter table public.resume_languages enable row level security;

-- ---- parent document ----
drop policy if exists "own resume select" on public.resume_profiles;
create policy "own resume select"
    on public.resume_profiles
    for select
    to authenticated
    using (auth.uid() = user_id);

drop policy if exists "own resume insert" on public.resume_profiles;
create policy "own resume insert"
    on public.resume_profiles
    for insert
    to authenticated
    with check (auth.uid() = user_id);

drop policy if exists "own resume update" on public.resume_profiles;
create policy "own resume update"
    on public.resume_profiles
    for update
    to authenticated
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

drop policy if exists "own resume delete" on public.resume_profiles;
create policy "own resume delete"
    on public.resume_profiles
    for delete
    to authenticated
    using (auth.uid() = user_id);

-- ---- child tables: ownership resolved through the parent document ----
-- The `exists` subquery is the whole security boundary for these tables: a row
-- is visible only when its parent resume belongs to auth.uid().
do $$
declare
    child text;
begin
    foreach child in array array[
        'resume_education', 'resume_experience', 'resume_references',
        'resume_projects', 'resume_publications', 'resume_skills',
        'resume_hobbies', 'resume_awards', 'resume_activities',
        'resume_languages'
    ]
    loop
        execute format('drop policy if exists %I on public.%I', 'own ' || child || ' select', child);
        execute format(
            'create policy %I on public.%I for select to authenticated using ('
            ' exists (select 1 from public.resume_profiles rp'
            ' where rp.id = resume_id and rp.user_id = auth.uid())'
            ')',
            'own ' || child || ' select', child);

        execute format('drop policy if exists %I on public.%I', 'own ' || child || ' insert', child);
        execute format(
            'create policy %I on public.%I for insert to authenticated with check ('
            ' exists (select 1 from public.resume_profiles rp'
            ' where rp.id = resume_id and rp.user_id = auth.uid())'
            ')',
            'own ' || child || ' insert', child);

        execute format('drop policy if exists %I on public.%I', 'own ' || child || ' update', child);
        execute format(
            'create policy %I on public.%I for update to authenticated'
            ' using (exists (select 1 from public.resume_profiles rp'
            ' where rp.id = resume_id and rp.user_id = auth.uid()))'
            ' with check (exists (select 1 from public.resume_profiles rp'
            ' where rp.id = resume_id and rp.user_id = auth.uid()))',
            'own ' || child || ' update', child);

        execute format('drop policy if exists %I on public.%I', 'own ' || child || ' delete', child);
        execute format(
            'create policy %I on public.%I for delete to authenticated using ('
            ' exists (select 1 from public.resume_profiles rp'
            ' where rp.id = resume_id and rp.user_id = auth.uid())'
            ')',
            'own ' || child || ' delete', child);
    end loop;
end
$$;

-- ===================================================================
-- Storage bucket for resume images
-- ===================================================================
-- Private (public = false): objects are never world-readable. The backend
-- mints short-lived signed URLs when the resume is read. Files live under a
-- per-user folder "<user_id>/..." and the storage policies below restrict
-- every operation to the caller's own folder, so one user cannot list, read,
-- overwrite or delete another user's uploads.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
    'resume-assets', 'resume-assets', false, 2097152,
    array['image/jpeg', 'image/png']
)
on conflict (id) do update
    set public = false,
        file_size_limit = excluded.file_size_limit,
        allowed_mime_types = excluded.allowed_mime_types;

-- foldername(name) returns the path segments as text[], so [1] is the user id.
drop policy if exists "own resume assets select" on storage.objects;
create policy "own resume assets select"
    on storage.objects
    for select
    to authenticated
    using (
        bucket_id = 'resume-assets'
        and (storage.foldername(name))[1] = auth.uid()::text
    );

drop policy if exists "own resume assets insert" on storage.objects;
create policy "own resume assets insert"
    on storage.objects
    for insert
    to authenticated
    with check (
        bucket_id = 'resume-assets'
        and (storage.foldername(name))[1] = auth.uid()::text
    );

drop policy if exists "own resume assets update" on storage.objects;
create policy "own resume assets update"
    on storage.objects
    for update
    to authenticated
    using (
        bucket_id = 'resume-assets'
        and (storage.foldername(name))[1] = auth.uid()::text
    )
    with check (
        bucket_id = 'resume-assets'
        and (storage.foldername(name))[1] = auth.uid()::text
    );

drop policy if exists "own resume assets delete" on storage.objects;
create policy "own resume assets delete"
    on storage.objects
    for delete
    to authenticated
    using (
        bucket_id = 'resume-assets'
        and (storage.foldername(name))[1] = auth.uid()::text
    );

-- ===================================================================
-- updated_at maintenance
-- ===================================================================
-- Keeps `updated_at` honest on every UPDATE without the application layer
-- having to remember to set it. Idempotent: re-running replaces the trigger.
create or replace function public.set_resume_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

do $$
declare
    t text;
begin
    foreach t in array array[
        'resume_profiles', 'resume_education', 'resume_experience',
        'resume_references', 'resume_projects', 'resume_publications'
    ]
    loop
        execute format('drop trigger if exists %I on public.%I', t || '_set_updated_at', t);
        execute format(
            'create trigger %I before update on public.%I'
            ' for each row execute function public.set_resume_updated_at()',
            t || '_set_updated_at', t
        );
    end loop;
end
$$;

-- ===================================================================
-- Verification
-- ===================================================================
-- select relname, relrowsecurity from pg_class
--  where relname like 'resume_%' order by relname;
--   -- expect every row to show relrowsecurity = t
--
-- select tablename, policyname, cmd from pg_policies
--  where tablename like 'resume_%' order by tablename, cmd;
--
-- select policyname, cmd from pg_policies
--  where tablename = 'objects' and policyname like 'own resume assets%';
--
-- select id, public, file_size_limit, allowed_mime_types from storage.buckets
--  where id = 'resume-assets';