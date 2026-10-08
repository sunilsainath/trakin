import { defineConfig, devices } from '@playwright/test'

/**
 * End-to-end: a real browser against real servers.
 *
 * Run the API (`python -m app.serve --port 8000` from apps/api) and the web
 * app (`npm run dev -- --port 3000` from apps/web) first, with the database
 * migrated. Specs sign in as a seeded demo account, so they assert states
 * that hold for any workspace (redirects, shells, validation messages)
 * rather than fixture-specific data.
 *
 *   E2E_EMAIL / E2E_PASSWORD  demo credentials (default: the local demo user)
 *   E2E_BASE_URL              web app under test (default: http://localhost:3000)
 */
export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  // Specs share one signed-in session file, so they run serially: parallel
  // workers would race token refreshes against each other.
  workers: 1,
  retries: process.env.CI ? 2 : 0,
  reporter: [['list']],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:3000',
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'setup', testMatch: /.*\.setup\.ts/ },
    {
      name: 'public',
      testMatch: /auth\.spec\.ts/,
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'chromium',
      testMatch: /(workspace|business)\.spec\.ts/,
      use: { ...devices['Desktop Chrome'], storageState: 'e2e/.auth/user.json' },
      dependencies: ['setup'],
    },
  ],
})
