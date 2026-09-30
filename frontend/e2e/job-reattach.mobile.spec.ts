import { expect, test } from '@playwright/test'

import { withTranslateLines } from './stageLineMocks'

// Phone: the "bulk batch pending" reason fits without sideways scroll.
test('a pending bulk batch reason fits a phone', async ({ page }) => {
  await page.route('**/api/jobs/bulk_translate_1', (route) => route.fulfill({
    json: {
      job_id: 'bulk_translate_1', status: 'running', progress: null, message: '', error: null, description: null,
      gpu_touching: false, started_at: 1, finished_at: null, updated_at: Date.now() / 1000,
    },
  }))
  await withTranslateLines(page)
  await page.goto('/#/drama/1/translate')
  await expect(page.getByTestId('translate-busy')).toContainText('Bulk batches below')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})
