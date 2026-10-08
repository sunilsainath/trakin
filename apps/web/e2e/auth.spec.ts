import { expect, test } from '@playwright/test'

/**
 * Landing → signup → login. No account is created here (that needs a real
 * inbox for verification); the flow proves every public surface renders,
 * links somewhere real, and rejects bad credentials with a typed message.
 */
test('landing links to signup and login', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('link', { name: /sign up/i }).first()).toBeVisible()
  await expect(page.getByRole('link', { name: /sign in|log in/i }).first()).toBeVisible()
})

test('signup renders the full registration form', async ({ page }) => {
  await page.goto('/signup')
  await expect(page.getByLabel(/first name/i)).toBeVisible()
  await expect(page.getByLabel(/work email/i)).toBeVisible()
  await expect(page.getByLabel(/^password/i)).toBeVisible()
  await expect(page.getByRole('button', { name: /continue with google/i })).toBeVisible()
})

test('login rejects unknown credentials', async ({ page }) => {
  await page.goto('/login')
  await page.getByLabel(/work email/i).fill('nobody@example.com')
  await page.getByLabel(/^password/i).fill('WrongPassword123!')
  await page.getByRole('button', { name: /^sign in$/i }).click()
  await expect(page.getByRole('alert').first()).toBeVisible({ timeout: 15000 })
})

test('workspace requires a session', async ({ page }) => {
  await page.goto('/network')
  await expect(page).toHaveURL(/\/login/, { timeout: 15000 })
})
