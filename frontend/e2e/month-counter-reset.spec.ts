import { expect, test } from '@playwright/test'
import { openSettingsGroups } from './settingsNav'

// Settings > Spending: reset the month counter (confirm), see both figures, undo.
test('spending: reset this month\'s counter with a confirm, then undo', async ({ page }) => {
  const settingsRoute = '**/api/settings'
  let resetAt: string | null = null
  const status = () => ({
    month_spend_usd: 12.5,
    month_spend_counted_usd: resetAt ? 0 : 12.5,
    month_spend_reset_at: resetAt,
  })
  await page.route(settingsRoute, async (route) => {
    const res = await route.fetch()
    const body = await res.json()
    await route.fulfill({ json: { ...body, effective_monthly_cap_usd: 20, ...status() } })
  })
  await page.route('**/api/settings/month-counter/reset', (route) => {
    const before = status()
    resetAt = '2026-10-05T10:00:00.123456'
    return route.fulfill({ json: { before, after: status() } })
  })
  await page.route('**/api/settings/month-counter/undo', (route) => {
    const before = status()
    resetAt = null
    return route.fulfill({ json: { before, after: status() } })
  })
  await page.goto('/#/settings')
  await openSettingsGroups(page)
  const box = page.getByTestId('month-counter')
  await expect(box).toContainText('Keeps your history, starts counting from now. The cap stays at $20.00.')
  await expect(page.getByTestId('month-spend')).toHaveText('This month: $12.50')
  await box.getByRole('button', { name: /Reset this month's counter/ }).click()
  await box.getByRole('button', { name: /Confirm reset/ }).click()
  await expect(page.getByTestId('month-spend')).toContainText('counted toward the cap since reset: $0.00')
  await expect(page.getByTestId('month-reset-at')).toBeVisible()
  await box.getByRole('button', { name: 'Undo reset' }).click()
  await expect(page.getByTestId('month-spend')).toHaveText('This month: $12.50')
  await expect(page.getByTestId('month-reset-at')).toHaveCount(0)
})
