# MyTrakin — Authentication

Supabase Auth owns credentials. The API never accepts, stores, or issues a
password (`app/api/v1/auth.py`).

## Flows (all in `apps/web`)

- `/signup`: first/last/email/mobile/address/country/password via
  `supabase.auth.signUp` with profile metadata; confirmation email required.
- `/login`: password, magic link (`shouldCreateUser: false`), Google OAuth.
  Users with an enrolled authenticator complete an MFA challenge step inline.
- `/auth/callback`: PKCE code exchange for OAuth, email confirmation, magic
  and recovery links; honours `?next=`.
- `/forgot-password` → emailed link → `/auth/callback?next=/reset-password` →
  `/reset-password` (`updateUser`).
- `/settings/security`: change password (`updateUser`), TOTP enroll/challenge/
  verify/unenroll, session list + revoke one/all (`GET/POST /auth/sessions*`).

## Verification (read this before touching it)

Supabase access tokens carry **no** `email_verified` claim (verified by
decoding a live token). Enforcement therefore lives in the platform row:

1. `provision_user` creates `PENDING_VERIFICATION` rows (NULL timestamp).
2. `POST /auth/bootstrap` (called on every web session start) syncs
   `email_confirmed_at` from the Supabase Admin API and flips confirmed users
   to `ACTIVE`. Best-effort: without Supabase it logs and skips.
3. `assert_account_active` raises `EMAIL_NOT_VERIFIED` (403) when the flag
   `REQUIRE_EMAIL_VERIFICATION` is on and the row is unconfirmed — or always
   for `PENDING_VERIFICATION`/`SUSPENDED`/`DEACTIVATED`.
4. The DB itself forbids `ACTIVE` without a timestamp
   (`ck_user_email_verified_status`).

`ck_user_email_verified_status` means test fixtures provision with
`verified=True` (see `conftest.py`).

## Sessions

Supabase sessions (localStorage, auto-refresh, PKCE) + platform session rows
for device tracking/revocation. No never-expiring sessions:
`SESSION_MAX_AGE_SECONDS` (default 30d) plus revocation on demand.
