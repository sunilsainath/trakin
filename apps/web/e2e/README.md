# End-to-end tests (Playwright)

A real browser against real servers — no mocks, no fixtures in code.

## Prerequisites

1. The database migrated (`python scripts/apply_migrations.py` from the repo root).
2. The API running: `python -m app.serve --port 8000` from `apps/api`.
3. The web app running: `npm run start -- --port 3000` from `apps/web`
   (build first; the dev server's hot-reload makes assertions flaky).
4. A seeded demo account the specs can sign in as. The specs default to
   `demo@mytrakin.app`, which must exist with a verified email, a name,
   and `onboarding_completed_at` set — otherwise every workspace route
   bounces to onboarding and the workspace specs fail honestly:

   ```sql
   UPDATE public.users
      SET first_name = COALESCE(NULLIF(first_name, ''), 'Demo'),
          last_name = COALESCE(NULLIF(last_name, ''), 'User'),
          onboarding_completed_at = COALESCE(onboarding_completed_at, now())
    WHERE email = 'demo@mytrakin.app';
   ```

   Override with `E2E_EMAIL` / `E2E_PASSWORD`; the base URL with
   `E2E_BASE_URL` (default `http://localhost:3000`).

## Run

```sh
npm run test:e2e
```

## Layout

- `e2e/auth.setup.ts` — signs in once and saves the session to
  `e2e/.auth/user.json` (git-ignored: it holds live tokens). Repeated
  password logins trip the login rate limiter, so specs never sign in
  themselves.
- `e2e/auth.spec.ts` — public surfaces: landing, signup form (including
  Google), login rejection, unauthenticated redirect. No session.
- `e2e/workspace.spec.ts` — feed, companies, invitation link states.
- `e2e/business.spec.ts` — company-creation W-9 validation, invoices
  receivable/payable toggle.

Specs assert states that hold for any workspace (redirects, shells,
validation messages) rather than fixture-specific data, and skip
gracefully where a seed lacks a company workspace.
