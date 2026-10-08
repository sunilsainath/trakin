-- =============================================================================
-- MyTrakin :: 0030_profile_visa_status.sql
--
-- Optional profile setup asks for visa status. It lives on user_profiles
-- (never on users), constrained to a closed set so the UI and the API cannot
-- drift apart, and readable under the same visibility rules as the rest of
-- the profile.
-- =============================================================================

ALTER TABLE public.user_profiles
  ADD COLUMN IF NOT EXISTS visa_status text;

ALTER TABLE public.user_profiles
  DROP CONSTRAINT IF EXISTS user_profiles_visa_status_check;

ALTER TABLE public.user_profiles
  ADD CONSTRAINT user_profiles_visa_status_check
  CHECK (
    visa_status IS NULL OR visa_status IN (
      'CITIZEN',
      'PERMANENT_RESIDENT',
      'WORK_VISA',
      'STUDENT_VISA',
      'OTHER',
      'PREFER_NOT_TO_SAY'
    )
  );
