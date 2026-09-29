import { expect, test, type Page } from '@playwright/test'

import { clearReviewResults, seedReviewResults } from './reviewResultsSeed'

// "Re-transcribe this line" in the Review line editor's details (parity
// audit B1, R23). Drama 3 has no audio in the seeded library, so the control
// is hidden there; the audio case mocks the config, the job start and the job
// polling (no Whisper in e2e).

test.beforeEach(() => seedReviewResults())
test.afterAll(() => clearReviewResults())

const SHOTS = process.env.REVIEW_SHOTS

const rows = (page: Page) => page.locator('.review-line:not(.review-skeleton)')

async function openDetails(page: Page, n: number) {
  const row = rows(page).nth(n)
  await row.locator('.review-idx').click()
  await row.getByRole('button', { name: 'Edit details' }).click()
  await expect(row.getByTestId('line-origin')).toBeVisible()
  return row
}

const job = (status: string, extra: Record<string, unknown> = {}) => ({
  job_id: 'retranscribe_3', status, progress: status === 'running' ? 0.4 : 1, message: status === 'running' ? 'Loading Whisper model small...' : '',
  error: null, description: null, gpu_touching: true, started_at: 1, finished_at: status === 'running' ? null : 2, updated_at: 1,
  ...extra,
})

test('hidden when the drama has no audio', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  await expect(rows(page)).toHaveCount(4)
  // wait for the config fetch to settle before asserting absence
  const config = page.waitForResponse((r) => r.url().includes('/api/transcribe/dramas/3/config'))
  const row = await openDetails(page, 1)
  await config
  await expect(row.getByTestId('retranscribe-line')).toHaveCount(0)
})

test('starts the job for this line id, shows progress, then the outcome', async ({ page }) => {
  let posted: { url: string; body: unknown } | null = null
  let polls = 0
  await page.route('**/api/transcribe/dramas/3/config', (route) =>
    route.fulfill({ json: { drama_id: 3, has_audio_pipeline: true, audio_available: true } }))
  await page.route('**/api/transcribe/dramas/3/lines/*/retranscribe', async (route) => {
    posted = { url: route.request().url(), body: route.request().postDataJSON() }
    const lineId = Number(route.request().url().split('/lines/')[1].split('/')[0])
    await route.fulfill({ json: { job_id: 'retranscribe_3', drama_id: 3, line_id: lineId } })
  })
  await page.route('**/api/jobs/retranscribe_3', (route) => {
    polls += 1
    return route.fulfill({
      json: polls < 3 ? job('running') : job('done', { result: { line_count: 1 }, outcome: 'ok', outcome_message: 'Finished.' }),
    })
  })

  await page.goto('/#/drama/3/review')
  await expect(rows(page)).toHaveCount(4)
  const lines = await (await page.request.get('/api/review/dramas/3/lines?page=1&page_size=50&only=all')).json()
  const target = lines.lines.find((l: { idx: number }) => l.idx === 1)
  expect(target).toBeTruthy()

  const row = await openDetails(page, 1)
  const box = row.getByTestId('retranscribe-line')
  const button = box.getByRole('button', { name: 'Re-transcribe this line' })
  await expect(button).toBeVisible()
  await button.click()

  await expect(box.getByTestId('retranscribe-progress')).toContainText('Loading Whisper model')
  await expect(box.getByRole('button', { name: 'Re-transcribing…' })).toBeDisabled()
  if (SHOTS) await row.screenshot({ path: `${SHOTS}/desktop-retranscribe-running.png` })

  await expect(box.getByTestId('retranscribe-result')).toContainText("Replaced this line's source text.")
  await expect(box.getByRole('button', { name: 'Re-transcribe this line' })).toBeEnabled()
  if (SHOTS) await row.screenshot({ path: `${SHOTS}/desktop-retranscribe-done.png` })

  expect(posted).not.toBeNull()
  expect(posted!.body).toEqual({})
  expect(posted!.url).toContain(`/lines/${target.id}/retranscribe`)
})

test('a failed run says the line was kept', async ({ page }) => {
  await page.route('**/api/transcribe/dramas/3/config', (route) =>
    route.fulfill({ json: { drama_id: 3, has_audio_pipeline: true, audio_available: true } }))
  await page.route('**/api/transcribe/dramas/3/lines/*/retranscribe', (route) =>
    route.fulfill({ json: { job_id: 'retranscribe_3', drama_id: 3, line_id: 1 } }))
  await page.route('**/api/jobs/retranscribe_3', (route) =>
    route.fulfill({ json: job('done', { result: { failed_reason: 'empty' }, outcome: 'failed' }) }))

  await page.goto('/#/drama/3/review')
  const row = await openDetails(page, 0)
  await row.getByRole('button', { name: 'Re-transcribe this line' }).click()
  await expect(row.getByTestId('retranscribe-result')).toContainText('No speech found')
})

test('a 409 from the server is shown', async ({ page }) => {
  await page.route('**/api/transcribe/dramas/3/config', (route) =>
    route.fulfill({ json: { drama_id: 3, has_audio_pipeline: true, audio_available: true } }))
  await page.route('**/api/transcribe/dramas/3/lines/*/retranscribe', (route) =>
    route.fulfill({ status: 409, json: { error: { code: 'conflict', message: "Another job is changing this drama's lines. Try again when it finishes." } } }))

  await page.goto('/#/drama/3/review')
  const row = await openDetails(page, 0)
  await row.getByRole('button', { name: 'Re-transcribe this line' }).click()
  await expect(row.getByTestId('retranscribe-line').getByRole('alert')).toBeVisible()
  await expect(row.getByRole('button', { name: 'Re-transcribe this line' })).toBeEnabled()
})
