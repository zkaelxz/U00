import { type Page } from '@playwright/test'
import { openFoldFor } from './reviewFolds'

import { expect, test } from './fixtures'

import { clearReviewResults, seedReviewResults } from './reviewResultsSeed'

// Parity R49: the Review stage's Bulk switch for the AI checks. The engine
// config is the real one with its engine and bulk list overridden; job
// starts, the job record and the bulk batch list are mocked (no provider
// is contacted).

test.beforeEach(() => seedReviewResults())
test.afterAll(() => clearReviewResults())

const section = (page: Page, title: string) =>
  page.locator('details.section').filter({ has: page.locator(':scope > summary .section-title', { hasText: new RegExp(`^${title}$`) }) })

async function open(page: Page, title: string) {
  await openFoldFor(page, title)
  const s = section(page, title)
  if ((await s.getAttribute('open')) === null) await s.locator(':scope > summary').click()
  await expect(s).toHaveAttribute('open', '')
  return s
}

const job = (id: string, status: string, message = '') => ({
  job_id: id, status, progress: null, message, error: null, description: null,
  gpu_touching: false, started_at: 1, finished_at: status === 'running' ? null : 2, updated_at: 1,
})

const batch = {
  bulk_job_id: 21, engine: 'claude', model: 'claude-sonnet', kind: 'consistency', stage: null, pipeline_id: null,
  status: 'submitted', pending: true, cancellable: true, line_count: 4, scheduled_for: null,
  result_summary: null, last_error: null, submitted_at: '2026-09-30T09:00:00', updated_at: '2026-09-30T09:01:00',
}

// The drama's engine and the server's bulk list (free tier off: Gemini is in it).
async function mockConfig(page: Page, engine: string, bulk = ['claude', 'gemini', 'deepseek']) {
  // Read the real config once: re-fetching per call can fail with "Response has been disposed".
  let real: Promise<Record<string, unknown>> | null = null
  await page.route('**/api/translate-run/dramas/3/config', async (route) => {
    real ??= route.fetch().then((r) => r.json())
    await route.fulfill({ json: { ...(await real), translation_engine: engine, bulk_supported_engines: bulk } })
  })
}

test('Bulk sends bulk only when switched on, then shows the pending batch and the edit warning', async ({ page }) => {
  await mockConfig(page, 'claude')
  const bodies: unknown[] = []
  let submitted = false
  await page.route('**/api/review-jobs/dramas/3/consistency', async (route) => {
    const body = route.request().postDataJSON() as { bulk?: boolean }
    bodies.push(body)
    if (body.bulk) {
      submitted = true
      await route.fulfill({ json: { job_id: 'bulk_consistency_3', drama_id: 3, kind: 'consistency', engine: 'claude', model: null, line_count: 4, bulk: true } })
    } else {
      await route.fulfill({ json: { job_id: 'cj', drama_id: 3, kind: 'consistency', engine: 'claude', model: null, line_count: 4, bulk: false } })
    }
  })
  await page.route('**/api/jobs/cj', (route) => route.fulfill({ json: job('cj', 'done') }))
  await page.route('**/api/jobs/bulk_consistency_3', (route) =>
    route.fulfill({ json: job('bulk_consistency_3', 'running', 'Waiting for the provider to finish the batch') }))
  await page.route('**/api/translate-run/dramas/3/bulk', (route) =>
    route.fulfill({ json: { drama_id: 3, jobs: submitted ? [batch] : [] } }))

  await page.goto('/#/drama/3/review')
  const ai = await open(page, 'AI review')
  const row = ai.getByTestId('review-job-consistency')
  const bulk = row.getByRole('switch', { name: 'Bulk: Check consistency' })
  await expect(bulk).toBeEnabled()
  await expect(bulk).toHaveAttribute('aria-checked', 'false')
  await expect(ai.getByText(/^Still needed for Bulk/)).toHaveCount(0)
  // No batches yet: the panel is not shown.
  await expect(page.getByRole('region', { name: 'Bulk batches' })).toHaveCount(0)

  // Off: nothing extra is sent.
  await row.getByRole('button', { name: 'Check consistency' }).click()
  await expect.poll(() => bodies.length).toBe(1)
  expect(bodies[0]).toEqual({})

  // The help says what Bulk costs and what it pauses (focus opens it, as a tap does).
  await row.getByRole('button', { name: 'Help: Bulk' }).focus()
  await expect(row.getByRole('tooltip')).toContainText('up to 24 hours')
  await expect(row.getByRole('tooltip')).toContainText('restoring a version and deleting the drama are refused')

  // On: only this check sends bulk, and the warning shows before starting.
  await bulk.click()
  await expect(bulk).toHaveAttribute('aria-checked', 'true')
  await expect(ai.getByTestId('review-job-flag').getByRole('switch')).toHaveAttribute('aria-checked', 'false')
  await expect(ai.getByTestId('bulk-warning')).toContainText('structural edits')
  await row.getByRole('button', { name: 'Check consistency' }).click()
  await expect.poll(() => bodies.length).toBe(2)
  expect(bodies[1]).toEqual({ bulk: true })

  // The bulk job has its own panel and notice; the other checks stay usable.
  const run = page.getByRole('group', { name: 'Bulk: Consistency check' })
  await expect(run.getByTestId('job-panel')).toBeVisible()
  await expect(run.getByRole('status')).toContainText('half price')
  await expect(run.getByRole('status')).toContainText('structural edits')
  await expect(ai.getByRole('button', { name: 'Tag emotion' })).toBeEnabled()

  // The pending batch is listed with its two-step cancel.
  const batches = page.getByRole('region', { name: 'Bulk batches' })
  await expect(batches.getByTestId('bulk-batch')).toHaveCount(1)
  await expect(batches.getByTestId('bulk-batch')).toContainText('Consistency check')
  await expect(batches.getByRole('button', { name: 'Cancel batch 21' })).toBeVisible()
})

test('Bulk is off and explained for an engine without a batch API', async ({ page }) => {
  await mockConfig(page, 'deepseek')
  const bodies: unknown[] = []
  await page.route('**/api/review-jobs/dramas/3/flag', async (route) => {
    bodies.push(route.request().postDataJSON())
    await route.fulfill({ json: { job_id: 'fj', drama_id: 3, kind: 'flag', engine: 'deepseek', model: null, line_count: 4 } })
  })
  await page.route('**/api/jobs/fj', (route) => route.fulfill({ json: job('fj', 'done') }))
  await page.goto('/#/drama/3/review')
  const ai = await open(page, 'AI review')
  await expect(ai.getByText('Still needed for Bulk: Claude or Gemini as the engine (Check options).')).toBeVisible()
  for (const sw of await ai.getByRole('list', { name: 'AI checks to run' }).getByRole('switch').all()) await expect(sw).toBeDisabled()
  await ai.getByRole('button', { name: 'Flag lines for a second look' }).click()
  await expect.poll(() => bodies.length).toBe(1)
  expect(bodies[0]).toEqual({})

  // Picking Claude under Check options makes Bulk available.
  const opts = await open(page, 'Check options')
  await opts.getByRole('combobox', { name: 'AI engine' }).selectOption('claude')
  await expect(ai.getByRole('switch', { name: 'Bulk: Flag lines for a second look' })).toBeEnabled()
  await expect(ai.getByText(/^Still needed for Bulk/)).toHaveCount(0)
})

test('Gemini on its free tier: Bulk points to Settings', async ({ page }) => {
  await mockConfig(page, 'gemini', ['claude', 'deepseek'])
  await page.goto('/#/drama/3/review')
  const ai = await open(page, 'AI review')
  await expect(ai.getByRole('switch', { name: 'Bulk: Tag emotion' })).toBeDisabled()
  await expect(ai.getByText(/Still needed for Bulk: Gemini's free tier turned off in Settings/)).toBeVisible()
  await expect(ai.getByRole('link', { name: 'Open Settings' })).toHaveAttribute('href', '#/settings')
})

test('Bulk says why it is off when the engine settings could not be loaded', async ({ page }) => {
  await page.route('**/api/translate-run/dramas/3/config', (route) =>
    route.fulfill({ status: 500, json: { error: { code: 'internal', message: 'boom' } } }))
  await page.goto('/#/drama/3/review')
  const ai = page.getByRole('group', { name: 'AI checks' })
  const summary = ai.locator('summary', { hasText: 'AI review' }).first()
  if ((await summary.locator('xpath=..').getAttribute('open')) === null) await summary.click()
  await expect(ai.getByRole('switch', { name: 'Bulk: Check consistency' })).toBeDisabled()
  await expect(ai).toContainText("Bulk is off: couldn't load the engine settings. Reload to try again.")
})
