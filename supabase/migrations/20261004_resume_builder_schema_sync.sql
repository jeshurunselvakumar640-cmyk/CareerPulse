-- ============================================================================
-- CareerPulse — Resume Builder Live Schema Sync
-- ============================================================================
-- PURPOSE:
-- Bring the EXISTING live Resume Builder schema into alignment with the
-- current app/services/resume_service.py implementation.
--
-- IMPORTANT:
-- This is a corrective migration.
-- It does NOT recreate or delete the Resume Builder tables.
--
-- Expected existing tables:
--   resume_profiles
--   resume_education
--   resume_experience
--   resume_references
--   resume_projects
--   resume_skills
--   resume_hobbies
--   resume_awards
--   resume_activities
--   resume_publications
--   resume_languages
--
-- The five value-list tables are currently empty, so their old columns can
-- safely be replaced with the shared `value` column.
-- ============================================================================


-- ============================================================================
-- 1. RESUME PROFILE
-- ============================================================================
-- The application creates an empty resume row before the user fills the
-- compulsory fields.
--
-- Therefore these fields must be nullable during initial creation.
-- ============================================================================

ALTER TABLE public.resume_profiles
    ALTER COLUMN name DROP NOT NULL;

ALTER TABLE public.resume_profiles
    ALTER COLUMN email DROP NOT NULL;

ALTER TABLE public.resume_profiles
    ALTER COLUMN headline DROP NOT NULL;


-- ============================================================================
-- 2. CHILD TABLES — ADD sort_order
-- ============================================================================
-- The application reads child records using:
--
--     ORDER BY sort_order
--
-- Add the column to every child table if it does not already exist.
-- ============================================================================

DO $$
DECLARE
    table_name TEXT;
BEGIN

    FOREACH table_name IN ARRAY ARRAY[
        'resume_education',
        'resume_experience',
        'resume_references',
        'resume_projects',
        'resume_skills',
        'resume_hobbies',
        'resume_awards',
        'resume_activities',
        'resume_publications',
        'resume_languages'
    ]
    LOOP

        EXECUTE format(
            'ALTER TABLE public.%I
             ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 0',
            table_name
        );

    END LOOP;

END
$$;


-- ============================================================================
-- 3. VALUE-LIST TABLES
-- ============================================================================
-- The application expects every simple list table to use:
--
--     value TEXT
--
-- The old live schema uses:
--
--     resume_skills      -> skill
--     resume_hobbies     -> hobby
--     resume_awards      -> award
--     resume_activities  -> activity
--     resume_languages   -> language
--
-- These tables were verified to contain zero rows before this migration.
-- Therefore the old columns can be removed safely.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- Skills
-- ----------------------------------------------------------------------------

ALTER TABLE public.resume_skills
    ADD COLUMN IF NOT EXISTS value TEXT NOT NULL DEFAULT '';

ALTER TABLE public.resume_skills
    DROP COLUMN IF EXISTS skill;


-- ----------------------------------------------------------------------------
-- Hobbies
-- ----------------------------------------------------------------------------

ALTER TABLE public.resume_hobbies
    ADD COLUMN IF NOT EXISTS value TEXT NOT NULL DEFAULT '';

ALTER TABLE public.resume_hobbies
    DROP COLUMN IF EXISTS hobby;


-- ----------------------------------------------------------------------------
-- Awards
-- ----------------------------------------------------------------------------

ALTER TABLE public.resume_awards
    ADD COLUMN IF NOT EXISTS value TEXT NOT NULL DEFAULT '';

ALTER TABLE public.resume_awards
    DROP COLUMN IF EXISTS award;


-- ----------------------------------------------------------------------------
-- Activities
-- ----------------------------------------------------------------------------

ALTER TABLE public.resume_activities
    ADD COLUMN IF NOT EXISTS value TEXT NOT NULL DEFAULT '';

ALTER TABLE public.resume_activities
    DROP COLUMN IF EXISTS activity;


-- ----------------------------------------------------------------------------
-- Languages
-- ----------------------------------------------------------------------------

ALTER TABLE public.resume_languages
    ADD COLUMN IF NOT EXISTS value TEXT NOT NULL DEFAULT '';

ALTER TABLE public.resume_languages
    DROP COLUMN IF EXISTS language;


-- ============================================================================
-- 4. ENSURE STORAGE BUCKET EXISTS
-- ============================================================================
-- Bucket:
--     resume-assets
--
-- Requirements:
--     private
--     maximum file size = 2 MiB
--     JPEG only
--     PNG only
--
-- 2 MiB = 2,097,152 bytes.
-- ============================================================================

INSERT INTO storage.buckets (
    id,
    name,
    public,
    file_size_limit,
    allowed_mime_types
)
VALUES (
    'resume-assets',
    'resume-assets',
    FALSE,
    2097152,
    ARRAY[
        'image/jpeg',
        'image/png'
    ]::text[]
)
ON CONFLICT (id)
DO UPDATE SET
    name = EXCLUDED.name,
    public = FALSE,
    file_size_limit = 2097152,
    allowed_mime_types = ARRAY[
        'image/jpeg',
        'image/png'
    ]::text[];


-- ============================================================================
-- 5. STORAGE POLICIES
-- ============================================================================
-- Users may access only files inside:
--
--     resume-assets/<their-auth-user-id>/...
--
-- RLS on storage.objects remains enabled.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- SELECT
-- ----------------------------------------------------------------------------

DROP POLICY IF EXISTS "resume_assets_select_own"
ON storage.objects;

DROP POLICY IF EXISTS "own resume assets select"
ON storage.objects;

CREATE POLICY "resume_assets_select_own"
ON storage.objects
FOR SELECT
TO authenticated
USING (
    bucket_id = 'resume-assets'
    AND (storage.foldername(name))[1] = auth.uid()::text
);


-- ----------------------------------------------------------------------------
-- INSERT
-- ----------------------------------------------------------------------------

DROP POLICY IF EXISTS "resume_assets_insert_own"
ON storage.objects;

DROP POLICY IF EXISTS "own resume assets insert"
ON storage.objects;

CREATE POLICY "resume_assets_insert_own"
ON storage.objects
FOR INSERT
TO authenticated
WITH CHECK (
    bucket_id = 'resume-assets'
    AND (storage.foldername(name))[1] = auth.uid()::text
);


-- ----------------------------------------------------------------------------
-- UPDATE
-- ----------------------------------------------------------------------------

DROP POLICY IF EXISTS "resume_assets_update_own"
ON storage.objects;

DROP POLICY IF EXISTS "own resume assets update"
ON storage.objects;

CREATE POLICY "resume_assets_update_own"
ON storage.objects
FOR UPDATE
TO authenticated
USING (
    bucket_id = 'resume-assets'
    AND (storage.foldername(name))[1] = auth.uid()::text
)
WITH CHECK (
    bucket_id = 'resume-assets'
    AND (storage.foldername(name))[1] = auth.uid()::text
);


-- ----------------------------------------------------------------------------
-- DELETE
-- ----------------------------------------------------------------------------

DROP POLICY IF EXISTS "resume_assets_delete_own"
ON storage.objects;

DROP POLICY IF EXISTS "own resume assets delete"
ON storage.objects;

CREATE POLICY "resume_assets_delete_own"
ON storage.objects
FOR DELETE
TO authenticated
USING (
    bucket_id = 'resume-assets'
    AND (storage.foldername(name))[1] = auth.uid()::text
);


-- ============================================================================
-- 6. VERIFICATION
-- ============================================================================
-- These SELECT statements are intentionally included so the SQL Editor
-- displays the resulting live state after the migration completes.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- Resume profile nullability
-- ----------------------------------------------------------------------------

SELECT
    table_name,
    column_name,
    is_nullable,
    data_type
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'resume_profiles'
  AND column_name IN ('name', 'email', 'headline')
ORDER BY column_name;


-- ----------------------------------------------------------------------------
-- sort_order on all child tables
-- ----------------------------------------------------------------------------

SELECT
    table_name,
    column_name,
    is_nullable,
    data_type,
    column_default
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name IN (
      'resume_education',
      'resume_experience',
      'resume_references',
      'resume_projects',
      'resume_skills',
      'resume_hobbies',
      'resume_awards',
      'resume_activities',
      'resume_publications',
      'resume_languages'
  )
  AND column_name = 'sort_order'
ORDER BY table_name;


-- ----------------------------------------------------------------------------
-- Value-list columns
-- ----------------------------------------------------------------------------

SELECT
    table_name,
    column_name,
    is_nullable,
    data_type
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name IN (
      'resume_skills',
      'resume_hobbies',
      'resume_awards',
      'resume_activities',
      'resume_languages'
  )
ORDER BY table_name, column_name;


-- ----------------------------------------------------------------------------
-- Storage bucket configuration
-- ----------------------------------------------------------------------------

SELECT
    id,
    name,
    public,
    file_size_limit,
    allowed_mime_types
FROM storage.buckets
WHERE id = 'resume-assets';


-- ----------------------------------------------------------------------------
-- RLS status
-- ----------------------------------------------------------------------------

SELECT
    schemaname,
    tablename,
    rowsecurity
FROM pg_tables
WHERE schemaname = 'public'
  AND tablename IN (
      'resume_profiles',
      'resume_education',
      'resume_experience',
      'resume_references',
      'resume_projects',
      'resume_skills',
      'resume_hobbies',
      'resume_awards',
      'resume_activities',
      'resume_publications',
      'resume_languages'
  )
ORDER BY tablename;
