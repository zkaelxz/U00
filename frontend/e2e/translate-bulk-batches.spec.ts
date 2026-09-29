import { expect, test, type Page } from '@playwright/test'

// Bulk batches panel on the Translate stage. The bulk list, cancel and
// resume routes are mocked (no provider is contacted); config and the
// rest of the stage hit the real seeded API.

const entry = (id: number, over: object = {}) => ({
  bulk_job_id: id, engine: 'claude', model: 'claude-sonnet', kind: 'translate', stage: null, pipeline_id: null,
  status: 'submitted', pending: true, cancellable: true, line_count: 240, scheduled_for: null,
  result_summary: null, last_error: null, submitted_at: '2026-09-29T09:00:00', updated_at: '2026-09-29T09:05:00', ...over,
})

async function mockList(page: Page, jobs: object[]) {
  await page.route('**/api/translate-run/dramas/1/bulk', (route) =>
    route.fulfill({ json: { drama_id: 1, jobs } }))
}

const shots = process.env.SHOTS_DIR
test.use({ viewport: { width: 1440, height: 900 } })

test('lists pending batches, resumes, and cancels one after confirming', async ({ page }) => {
  await mockList(page, [
    entry(12),
    entry(11, { engine: 'deepseek', model: null, status: 'scheduled', scheduled_for: '2026-09-29T16:30:00', line_count: 80 }),
    entry(9, { status: 'applied', pending: false, cancellable: false, result_summary: { applied: 238, missing: 2 } }),
  ])
  const cancels: string[] = []
  await page.route('**/api/translate-run/dramas/1/bulk/*/cancel', async (route) => {
    cancels.push(route.request().url())
    await route.fulfill({
      json: {
        drama_id: 1,
        bulk_job: entry(12, { status: 'cancelled', pending: false, cancellable: false }),
        message: 'Cancelled at the provider and here.',
      },
    })
  })
  await page.route('**/api/translate-run/dramas/1/bulk/resume', (route) =>
    route.fulfill({ json: { drama_id: 1, jobs: [{ bulk_job_id: 11, state: 'polling' }, { bulk_job_id: 12, state: 'needs_key' }] } }))

  await page.goto('/#/drama/1/translate')
  const panel = page.getByRole('region', { name: 'Bulk batches' })
  // Open by default while something is pending; finished batches are hidden.
  await expect(panel.getByTestId('bulk-batch')).toHaveCount(2)
  await expect(panel.getByText('Waiting for the provider')).toBeVisible()
  await expect(panel.getByText('runs 2026-09-29 16:30')).toBeVisible()
  await panel.getByLabel(/Show finished/).check()
  await expect(panel.getByTestId('bulk-batch')).toHaveCount(3)
  await expect(panel.getByText('applied 238 · missing 2')).toBeVisible()

  await panel.getByRole('button', { name: 'Resume pending batches' }).click()
  await expect(panel.getByTestId('bulk-resume')).toHaveText('#11 now being checked · #12 needs an API key in Settings')

  // Two-step cancel: the first click only asks.
  await panel.getByRole('button', { name: 'Cancel batch 12' }).click()
  await expect(panel.getByText('Cancel batch #12?', { exact: false })).toBeVisible()
  if (shots) await page.screenshot({ path: `${shots}/desktop-confirm.png`, fullPage: true })
  await panel.getByRole('button', { name: 'Keep it' }).click()
  expect(cancels).toEqual([])
  await panel.getByRole('button', { name: 'Cancel batch 12' }).click()
  await panel.getByRole('button', { name: 'Yes, cancel it' }).click()
  await expect(panel.getByRole('status')).toHaveText('#12: Cancelled at the provider and here.')
  expect(cancels).toHaveLength(1)
  expect(cancels[0]).toMatch(/\/bulk\/12\/cancel$/)
  await expect(panel.getByRole('button', { name: 'Cancel batch 12' })).toHaveCount(0)
  await expect(panel.getByText('Cancelled', { exact: true })).toBeVisible()
  if (shots) await page.screenshot({ path: `${shots}/desktop-after-cancel.png`, fullPage: true })
})

test('a batch that finished meanwhile: the 409 is shown in plain words', async ({ page }) => {
  await mockList(page, [entry(5)])
  await page.route('**/api/translate-run/dramas/1/bulk/5/cancel', (route) =>
    route.fulfill({ status: 409, json: { error: { code: 'conflict', message: 'Bulk job 5 is applied and cannot be cancelled.' } } }))
  await page.goto('/#/drama/1/translate')
  const panel = page.getByRole('region', { name: 'Bulk batches' })
  await panel.getByRole('button', { name: 'Cancel batch 5' }).click()
  await panel.getByRole('button', { name: 'Yes, cancel it' }).click()
  await expect(panel.getByRole('alert')).toContainText('Bulk job 5 is applied and cannot be cancelled.')
})

test('empty state with no batches (real seeded API)', async ({ page }) => {
  const config = await (await page.request.get('/api/translate-run/dramas/1/config')).json()
  test.skip(config.bulk_supported_engines.length === 0, 'no bulk-capable engine in this build')
  await page.goto('/#/drama/1/translate')
  await page.locator('details.section', { hasText: 'Bulk batches' }).locator('summary').click()
  const panel = page.getByRole('region', { name: 'Bulk batches' })
  await expect(panel.getByText(/No bulk batches yet/)).toBeVisible()
  if (shots) await page.screenshot({ path: `${shots}/desktop-empty.png`, fullPage: true })
})
