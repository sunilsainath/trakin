import { expect, test as setup } from '@playwright/test'

/**
 * One sign-in for the whole run, saved as a storage state. Repeated
 * password logins trip the login rate limiter, so specs never sign in
 * themselves — they load this state instead.
 */
const EMAIL = process.env.E2E_EMAIL ?? 'demo@mytrakin.app'
const PASSWORD = process.env.E2E_PASSWORD ?? 'Trakin@123'
const AUTH_FILE = 'e2e/.auth/user.json'

setup('authenticate as the demo user', async ({ page }) => {
  await page.goto('/login')
  await page.getByLabel(/work email/i).fill(EMAIL)
  await page.getByLabel(/^password/i).fill(PASSWORD)
  await page.getByRole('button', { name: /^sign in$/i }).click()
  // Onboarded users land in the workspace; everyone else in onboarding.
  await expect(page).toHaveURL(/\/(network|onboarding|companies)/, { timeout: 30000 })
  await page.context().storageState({ path: AUTH_FILE })
})
