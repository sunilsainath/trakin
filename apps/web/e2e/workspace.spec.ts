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

/**
 * The rail lists every product module for every signed-in user, company or
 * not. Company modules render their own empty states instead of crashing,
 * so each of these must show a heading — never a blank page or a redirect
 * loop.
 */
for (const [path, heading] of [
  ['/projects', /projects/i],
  ['/sows', /statements of work/i],
  ['/contracts', /contracts/i],
  ['/invoices', /invoices/i],
  ['/payments', /payments/i],
  ['/time', /my time and leave/i],
  ['/connections', /connections/i],
  ['/messages', /messages/i],
] as const) {
  test(`module ${path} renders without a company`, async ({ page }) => {
    await page.goto(path)
    await expect(page.getByRole('heading', { name: heading }).first()).toBeVisible({
      timeout: 30000,
    })
  })
}

test('notifications surface renders without a company', async ({ page }) => {
  await page.goto('/notifications')
  await expect(page.getByText(/notification/i).first()).toBeVisible({ timeout: 30000 })
})

test('bogus invitation link explains itself', async ({ page }) => {
  await page.goto('/invitations/this-token-does-not-exist')
  await expect(page.getByText(/does not work/i)).toBeVisible({ timeout: 30000 })
})
