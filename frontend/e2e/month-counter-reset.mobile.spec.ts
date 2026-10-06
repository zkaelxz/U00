import { expect, test, type Page } from '@playwright/test'
import { openSettingsGroups } from './settingsNav'
import { hitHeight, installHitArea } from './hitArea'

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

// Phone project: the month-counter reset (confirm step, undo) fits 390px and keeps 44px targets.
async function noSideways(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
}

test('spending: reset with a confirm, then undo, on a phone', async ({ page }) => {
  let resetAt: string | null = null
  const status = () => ({
    month_spend_usd: 12.5,
    month_spend_counted_usd: resetAt ? 0 : 12.5,
    month_spend_reset_at: resetAt,
  })
  await page.route('**/api/settings', async (route) => {
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
  await expect(page.getByTestId('month-spend')).toHaveText('This month: $12.50')
  await noSideways(page)

  const reset = box.getByRole('button', { name: /Reset this month's counter/ })
  expect(await hitHeight(reset)).toBeGreaterThanOrEqual(44)
  await reset.tap()
  const confirm = box.getByRole('button', { name: /Confirm reset/ })
  await expect(confirm).toBeVisible()
  expect(await hitHeight(confirm)).toBeGreaterThanOrEqual(44)
  await noSideways(page)
  await confirm.tap()

  await expect(page.getByTestId('month-spend')).toContainText('counted toward the cap since reset: $0.00')
  await expect(page.getByTestId('month-reset-at')).toBeVisible()
  const undo = box.getByRole('button', { name: 'Undo reset' })
  expect(await hitHeight(undo)).toBeGreaterThanOrEqual(44)
  await noSideways(page)
  await undo.tap()
  await expect(page.getByTestId('month-spend')).toHaveText('This month: $12.50')
  await expect(page.getByTestId('month-reset-at')).toHaveCount(0)
  await noSideways(page)
})
