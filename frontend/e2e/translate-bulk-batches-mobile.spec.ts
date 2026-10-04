import { expect, test } from '@playwright/test'
import { installHitArea } from './hitArea'

// .btn-sm keeps a 44px hit area but is 32px tall: measure the hit area, not the box.
test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

// Phone project (390x844, touch): the bulk batches panel fits the width,
// uses the stacked list, and its buttons are at least 44px tall.

const entry = (id: number, over: object = {}) => ({
  bulk_job_id: id, engine: 'gemini', model: 'gemini-2.5-pro-with-a-long-model-name', kind: 'reflect', stage: 'critique',
  pipeline_id: 'p1', status: 'submitted', pending: true, cancellable: true, line_count: 1200, scheduled_for: null,
  result_summary: null, last_error: null, submitted_at: '2026-09-29T09:00:00', updated_at: '2026-09-29T09:05:00', ...over,
})

test('bulk batches panel on a phone', async ({ page }) => {
  await page.route('**/api/translate-run/dramas/1/bulk', (route) =>
    route.fulfill({
      json: { drama_id: 1, jobs: [entry(21), entry(20, { status: 'auth_error', last_error: 'The provider rejected the key.' })] },
    }))
  await page.goto('/#/drama/1/translate')
  const panel = page.getByRole('region', { name: 'Bulk batches' })
  await expect(panel.locator('.bulk-list li')).toHaveCount(2)
  await expect(panel.locator('table')).toHaveCount(0)
  await panel.getByRole('button', { name: 'Cancel batch 21' }).click()
  await expect(panel.getByRole('button', { name: 'Yes, cancel it' })).toBeVisible()

  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
  const heights = await panel.locator('button').evaluateAll((els) =>
    els.map((e) => ({ h: window.hitHeight(e), t: (e.textContent ?? '').trim() })))
  expect(heights.length).toBeGreaterThan(3)
  for (const { h, t } of heights) expect(h, t).toBeGreaterThanOrEqual(44)

  if (process.env.SHOTS_DIR) {
    await panel.scrollIntoViewIfNeeded()
    await page.screenshot({ path: `${process.env.SHOTS_DIR}/phone-confirm.png`, fullPage: true })
    await page.screenshot({ path: `${process.env.SHOTS_DIR}/phone-viewport.png` })
  }
})
