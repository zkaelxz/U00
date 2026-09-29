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
  const cancels: string[] = []
  const rest = [
    entry(11, { engine: 'deepseek', model: null, status: 'scheduled', scheduled_for: '2026-09-29T16:30:00', line_count: 80 }),
    entry(9, { status: 'applied', pending: false, cancellable: false, result_summary: { applied: 238, missing: 2 } }),
  ]
  // The server's list reflects the cancel once it has happened.
  await page.route('**/api/translate-run/dramas/1/bulk', (route) =>
    route.fulfill({
      json: { drama_id: 1, jobs: [cancels.length ? entry(12, { status: 'cancelled', pending: false, cancellable: false }) : entry(12), ...rest] },
    }))
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
  await panel.getByRole('switch', { name: /Show finished/ }).click()
  await expect(panel.getByTestId('bulk-batch')).toHaveCount(3)
  await expect(panel.getByText('applied 238 · missing 2')).toBeVisible()

  await panel.getByRole('button', { name: 'Resume pending batches' }).click()
  await expect(panel.getByTestId('bulk-resume')).toHaveText('#11 now being checked · #12 needs an API key in Settings')

  // Two-step cancel: the first click only asks.
  await panel.getByRole('button', { name: 'Cancel batch 12' }).click()
  await expect(panel.getByText('Cancel batch #12?', { exact: false })).toBeVisible()
  await expect(panel.getByRole('button', { name: 'Yes, cancel it' })).toBeFocused()
  if (shots) await page.screenshot({ path: `${shots}/desktop-confirm.png`, fullPage: true })
  await panel.getByRole('button', { name: 'Keep it' }).click()
  await expect(panel.getByRole('button', { name: 'Cancel batch 12' })).toBeFocused()
  expect(cancels).toEqual([])
  await panel.getByRole('button', { name: 'Cancel batch 12' }).click()
  await panel.getByRole('button', { name: 'Yes, cancel it' }).click()
  await expect(panel.getByRole('status')).toHaveText('#12: Cancelled at the provider and here.')
  // The row has no Cancel button any more, so focus lands on the note.
  await expect(panel.getByRole('status')).toBeFocused()
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

test('a list read still in flight when a cancel succeeds cannot bring the old status back', async ({ page }) => {
  let cancelled = false
  let slow = false
  await page.route('**/api/translate-run/dramas/1/bulk', async (route) => {
    const staleRead = slow && !cancelled
    if (staleRead) await new Promise((r) => setTimeout(r, 1500))
    const done = cancelled && !staleRead
    await route.fulfill({
      json: { drama_id: 1, jobs: [done ? entry(7, { status: 'cancelled', pending: false, cancellable: false }) : entry(7)] },
    })
  })
  await page.route('**/api/translate-run/dramas/1/bulk/7/cancel', async (route) => {
    cancelled = true
    await route.fulfill({
      json: { drama_id: 1, bulk_job: entry(7, { status: 'cancelled', pending: false, cancellable: false }), message: 'Cancelled.' },
    })
  })
  await page.goto('/#/drama/1/translate')
  const panel = page.getByRole('region', { name: 'Bulk batches' })
  await expect(panel.getByRole('button', { name: 'Cancel batch 7' })).toBeVisible()
  slow = true
  await panel.getByRole('button', { name: 'Refresh' }).click() // this read answers late, with the old status
  await panel.getByRole('button', { name: 'Cancel batch 7' }).click()
  await panel.getByRole('button', { name: 'Yes, cancel it' }).click()
  await expect(panel.getByRole('status')).toHaveText('#7: Cancelled.')
  await page.waitForTimeout(2000) // let the late read arrive
  await panel.getByRole('switch', { name: /Show finished/ }).click()
  await expect(panel.getByText('Cancelled', { exact: true })).toBeVisible()
  await expect(panel.getByText('Waiting for the provider')).toHaveCount(0)
})

test('no batches: the Bulk batches section is not rendered (real seeded API)', async ({ page }) => {
  await page.goto('/#/drama/1/translate')
  await expect(page.getByRole('region', { name: 'Translate run' })).toBeVisible()
  await page.waitForLoadState('networkidle')
  await expect(page.locator('details.section', { hasText: 'Bulk batches' })).toHaveCount(0)
  if (shots) await page.screenshot({ path: `${shots}/desktop-empty.png`, fullPage: true })
})
