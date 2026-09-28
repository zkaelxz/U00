import { expect, test } from '@playwright/test'

// Needs SettingsPage wired at #/settings (the lead does that); until then
// the placeholder is shown and this spec fails by design.
test('settings toggles round-trip and keys are yes/no only', async ({ page }) => {
  await page.goto('/#/settings')
  const box = page.getByRole('checkbox', { name: /Notify when a job finishes/ })
  await expect(box).toBeVisible()
  await expect(page.getByText(/API-key entry is not available/)).toBeVisible()
  const before = await box.isChecked()

  await box.click()
  await expect(box).toBeChecked({ checked: !before })
  await page.reload()
  await expect(box).toBeChecked({ checked: !before })

  await box.click() // restore
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
