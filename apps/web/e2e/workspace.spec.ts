import { expect, test } from '@playwright/test'

/**
 * Signed-in workspace. The `setup` project signs in once and every test
 * here loads that session — repeated password logins trip the login rate
 * limiter. Assertions hold for any workspace state (onboarded or not):
 * shells render, navigation works, and validation messages appear instead
 * of silent failures.
 */
test('feed shell renders navigation', async ({ page }) => {
  await page.goto('/network')
  await expect(page.getByRole('heading', { name: /network/i }).first()).toBeVisible({
    timeout: 30000,
  })
})

test('companies page renders with its states', async ({ page }) => {
  await page.goto('/companies')
  await expect(page.getByRole('heading', { name: /companies/i }).first()).toBeVisible({
    timeout: 30000,
  })
})

test('bogus invitation link explains itself', async ({ page }) => {
  await page.goto('/invitations/this-token-does-not-exist')
  await expect(page.getByText(/does not work/i)).toBeVisible({ timeout: 30000 })
})
