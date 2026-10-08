import { expect, test } from '@playwright/test'

/**
 * Business module surfaces under a signed-in session (see workspace.spec).
 * Creating records needs a company workspace, so these specs prove the
 * validation layer instead: required W-9s, required fields, and the review
 * step all speak before anything reaches the server.
 */
test('company creation requires a W-9 before anything else', async ({ page }) => {
  await page.goto('/companies')
  const heading = page.getByRole('heading', { name: /companies/i }).first()
  await expect(heading).toBeVisible({ timeout: 30000 })

  const opener = page.getByRole('button', { name: /new company|create a company/i }).first()
  // Company-less demo accounts see an empty state instead of the list.
  if (!(await opener.isVisible())) test.skip(true, 'no company workspace on this seed')
  await opener.click()
  await page.getByLabel(/legal name/i).fill('E2E Test LLC')
  const review = page.getByRole('button', { name: /review w-9/i })
  await review.scrollIntoViewIfNeeded()
  // Forced: the dialog's entrance animation replays on background re-renders,
  // so the button never reports "stable" although it is visible and enabled.
  await review.click({ force: true })
  await expect(page.getByText(/upload a w-9 first/i)).toBeVisible()
})

test('invoices list offers receivable and payable', async ({ page }) => {
  await page.goto('/invoices')
  await expect(page.getByRole('group', { name: /invoice direction/i })).toBeVisible({
    timeout: 30000,
  })
})
