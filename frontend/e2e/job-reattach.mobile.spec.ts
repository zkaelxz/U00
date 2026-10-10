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

const fits = (page: import('@playwright/test').Page) =>
  page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)

// Phone: a long failure message on the Last run card wraps, and the stuck
// warning with its Cancel fits beside it.
test('the Last run card and the stuck warning fit a phone', async ({ page }) => {
  const t = Math.floor(Date.now() / 1000)
  const base = {
    progress: null, message: '', description: null, gpu_touching: false, drama_id: 1, kind: 'translate',
    started_at: t - 100, finished_at: t - 50, updated_at: t - 50,
  }
  await page.route('**/api/jobs', (route) => route.fulfill({
    json: {
      count: 1,
      items: [{ ...base, job_id: 'translate_1', status: 'error', outcome: 'failed',
        error: 'Anthropic answered 529 overloaded_error after 3 attempts; the batch https-free message is long enough to need wrapping on a narrow screen.' }],
    },
  }))
  await page.route('**/api/jobs/translate_1', (route) =>
    route.fulfill({ status: 404, json: { error: { code: 'not_found', message: 'No job.' } } }))
  await withTranslateLines(page)
  await page.goto('/#/drama/1/translate')
  const card = page.getByTestId('last-run')
  await expect(card).toContainText('Failed')
  await expect(card.getByRole('button', { name: 'Retry' })).toBeVisible()
  expect(await fits(page)).toBe(true)

  await page.unroute('**/api/jobs/translate_1')
  await page.route('**/api/jobs/translate_1', (route) => route.fulfill({
    json: { ...base, job_id: 'translate_1', status: 'running', progress: 0.4, message: 'Batch 2 of 5', error: null, finished_at: null, stalled: true },
  }))
  await page.reload()
  await expect(page.getByTestId('job-stuck')).toContainText('It may be stuck.')
  await expect(page.getByRole('button', { name: 'Cancel job' })).toBeVisible()
  expect(await fits(page)).toBe(true)
})
