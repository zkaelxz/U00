import { expect, test } from '@playwright/test'

// Needs SettingsPage wired at #/settings (the lead does that); until then
// the placeholder is shown and this spec fails by design.
test('settings toggles round-trip and keys are yes/no only', async ({ page }) => {
  await page.goto('/#/settings')
  const box = page.getByRole('checkbox', { name: /Notify when a job finishes/ })
  await expect(box).toBeVisible()
  await expect(page.getByText(/API-key entry is not available/)).toBeVisible()
  const before = await box.isChecked()

  // The toggle updates optimistically; wait for the save to finish before reloading,
  // or the reload can read the old server value.
  const saved = () => page.waitForResponse((r) => r.url().endsWith('/api/settings') && r.request().method() === 'POST')
  await Promise.all([saved(), box.click()])
  await expect(box).toBeChecked({ checked: !before })
  await page.reload()
  await expect(box).toBeChecked({ checked: !before })

  await Promise.all([saved(), box.click()]) // restore
  await expect(box).toBeChecked({ checked: before })
  for (const dd of await page.locator('[data-testid^="key-"]').all())
    await expect(dd).toHaveText(/^(Yes|No)$/)
})

test('a failed update rolls the toggle back and shows an error', async ({ page }) => {
  await page.route('**/api/settings', (route) =>
    route.request().method() === 'POST'
      ? route.fulfill({
          status: 422,
          contentType: 'application/json',
          body: JSON.stringify({ error: { code: 'invalid_input', message: 'Rejected.' } }),
        })
      : route.continue(),
  )
  await page.goto('/#/settings')
  const box = page.getByRole('checkbox', { name: /Use the GPU/ })
  const before = await box.isChecked()
  await box.click()
  await expect(page.getByRole('alert')).toBeVisible()
  await expect(box).toBeChecked({ checked: before })
})

test('a collapsible section shows a summary, remembers its state and fits a phone', async ({ page }) => {
  await page.setViewportSize({ width: 400, height: 800 })
  await page.goto('/#/settings')
  const details = page.locator('details.section', { hasText: 'API keys configured' })
  await expect(details.locator('.section-summary')).toHaveText(/\d+ of \d+ configured/)
  await expect(details).not.toHaveAttribute('open', '')

  await details.locator('summary').click()
  await expect(details).toHaveAttribute('open', '')
  await expect(details.locator('.section-summary')).toHaveCount(0)
  expect(await page.evaluate(() => localStorage.getItem('baihe.section.settings.api-keys'))).toBe('1')

  await page.reload()
  await expect(page.locator('details.section', { hasText: 'API keys configured' })).toHaveAttribute('open', '')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.evaluate(() => localStorage.removeItem('baihe.section.settings.api-keys'))
})
