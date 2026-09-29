import { expect, test, type Page } from '@playwright/test'

import { clearReviewResults, seedReviewResults } from './reviewResultsSeed'

// "Re-transcribe this line" in the Review line editor's details (parity
// audit B1, R23). Drama 3 has no audio in the seeded library, so the control
// is hidden there; the audio case mocks the config, the job start and the job
// polling (no Whisper in e2e), and the apply route (it needs a finished job in
// the API process; pytest covers the real apply).

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

async function withAudio(page: Page) {
  await page.route('**/api/transcribe/dramas/3/config', (route) =>
    route.fulfill({ json: { drama_id: 3, has_audio_pipeline: true, audio_available: true } }))
}

test('hidden when the drama has no audio', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  await expect(rows(page)).toHaveCount(4)
  // wait for the config fetch to settle before asserting absence
  const config = page.waitForResponse((r) => r.url().includes('/api/transcribe/dramas/3/config'))
  const row = await openDetails(page, 1)
  await config
  await expect(row.getByTestId('retranscribe-line')).toHaveCount(0)
})

test('shows progress, then "Heard" next to the text; "Use this" applies it with the base text', async ({ page }) => {
  let started: string | null = null
  let applied: { url: string; body: unknown } | null = null
  let polls = 0
  await withAudio(page)
  await page.route('**/api/transcribe/dramas/3/lines/*/retranscribe', async (route) => {
    started = route.request().url()
    const lineId = Number(route.request().url().split('/lines/')[1].split('/')[0])
    await route.fulfill({ json: { job_id: 'retranscribe_3', drama_id: 3, line_id: lineId } })
  })
  await page.route('**/api/transcribe/dramas/3/lines/*/retranscribe/apply', async (route) => {
    applied = { url: route.request().url(), body: route.request().postDataJSON() }
    await route.fulfill({ json: { drama_id: 3, line_id: 1, zh: '魏婴来啦' } })
  })
  await page.route('**/api/jobs/retranscribe_3', (route) => {
    polls += 1
    return route.fulfill({
      json: polls < 3
        ? job('running')
        : job('done', { result: { proposed_zh: '魏婴来啦', base_zh: '魏婴来了' }, outcome: 'ok', outcome_message: 'Finished.' }),
    })
  })

  await page.goto('/#/drama/3/review')
  await expect(rows(page)).toHaveCount(4)
  const lines = await (await page.request.get('/api/review/dramas/3/lines?page=1&page_size=50&only=all')).json()
  const target = lines.lines.find((l: { idx: number }) => l.idx === 1)
  expect(target).toBeTruthy()

  const row = await openDetails(page, 1)
  const box = row.getByTestId('retranscribe-line')
  await box.getByRole('button', { name: 'Re-transcribe this line' }).click()

  await expect(box.getByTestId('retranscribe-progress')).toContainText('Loading Whisper model')
  await expect(box.getByRole('button', { name: 'Re-transcribing…' })).toBeDisabled()
  if (SHOTS) await row.screenshot({ path: `${SHOTS}/desktop-retranscribe-running.png` })

  const proposal = box.getByTestId('retranscribe-proposal')
  await expect(proposal).toContainText('Now')
  await expect(proposal).toContainText('魏婴来了')
  await expect(box.getByTestId('retranscribe-heard')).toHaveText('魏婴来啦')
  if (SHOTS) await row.screenshot({ path: `${SHOTS}/desktop-retranscribe-proposal.png` })
  expect(applied).toBeNull() // nothing written until "Use this"

  await proposal.getByRole('button', { name: 'Use this' }).click()
  await expect(box.getByTestId('retranscribe-result')).toContainText("Replaced this line’s source text.")
  await expect(box.getByTestId('retranscribe-proposal')).toHaveCount(0)
  if (SHOTS) await row.screenshot({ path: `${SHOTS}/desktop-retranscribe-applied.png` })

  expect(started).toContain(`/lines/${target.id}/retranscribe`)
  expect(applied!.url).toContain(`/lines/${target.id}/retranscribe/apply`)
  expect(applied!.body).toEqual({ job_id: 'retranscribe_3', expected_zh: '魏婴来了' })
})

test('"Discard" drops the proposal without writing', async ({ page }) => {
  let applied = false
  await withAudio(page)
  await page.route('**/api/transcribe/dramas/3/lines/*/retranscribe', (route) =>
    route.fulfill({ json: { job_id: 'retranscribe_3', drama_id: 3, line_id: 1 } }))
  await page.route('**/api/transcribe/dramas/3/lines/*/retranscribe/apply', (route) => {
    applied = true
    return route.fulfill({ json: { drama_id: 3, line_id: 1, zh: 'x' } })
  })
  await page.route('**/api/jobs/retranscribe_3', (route) =>
    route.fulfill({ json: job('done', { result: { proposed_zh: '别的', base_zh: '你好' }, outcome: 'ok' }) }))

  await page.goto('/#/drama/3/review')
  const row = await openDetails(page, 0)
  await row.getByRole('button', { name: 'Re-transcribe this line' }).click()
  await row.getByTestId('retranscribe-proposal').getByRole('button', { name: 'Discard' }).click()
  await expect(row.getByTestId('retranscribe-proposal')).toHaveCount(0)
  expect(applied).toBe(false)
})

test('a 409 on "Use this" is shown and the proposal stays', async ({ page }) => {
  await withAudio(page)
  await page.route('**/api/transcribe/dramas/3/lines/*/retranscribe', (route) =>
    route.fulfill({ json: { job_id: 'retranscribe_3', drama_id: 3, line_id: 1 } }))
  await page.route('**/api/transcribe/dramas/3/lines/*/retranscribe/apply', (route) =>
    route.fulfill({ status: 409, json: { error: { code: 'conflict', message: 'This line changed since it was re-transcribed; your edit was kept.' } } }))
  await page.route('**/api/jobs/retranscribe_3', (route) =>
    route.fulfill({ json: job('done', { result: { proposed_zh: '别的', base_zh: '你好' }, outcome: 'ok' }) }))

  await page.goto('/#/drama/3/review')
  const row = await openDetails(page, 0)
  await row.getByRole('button', { name: 'Re-transcribe this line' }).click()
  await row.getByTestId('retranscribe-proposal').getByRole('button', { name: 'Use this' }).click()
  await expect(row.getByTestId('retranscribe-line').getByRole('alert')).toBeVisible()
  await expect(row.getByTestId('retranscribe-proposal')).toBeVisible()
})

test('a failed run says why, with nothing to apply', async ({ page }) => {
  await withAudio(page)
  await page.route('**/api/transcribe/dramas/3/lines/*/retranscribe', (route) =>
    route.fulfill({ json: { job_id: 'retranscribe_3', drama_id: 3, line_id: 1 } }))
  await page.route('**/api/jobs/retranscribe_3', (route) =>
    route.fulfill({ json: job('done', { result: { failed_reason: 'empty' }, outcome: 'failed' }) }))

  await page.goto('/#/drama/3/review')
  const row = await openDetails(page, 0)
  await row.getByRole('button', { name: 'Re-transcribe this line' }).click()
  await expect(row.getByTestId('retranscribe-result')).toContainText('No speech found')
  await expect(row.getByRole('button', { name: 'Use this' })).toHaveCount(0)
})

test('a 409 on start is shown', async ({ page }) => {
  await withAudio(page)
  await page.route('**/api/transcribe/dramas/3/lines/*/retranscribe', (route) =>
    route.fulfill({ status: 409, json: { error: { code: 'conflict', message: "Another job is changing this drama's lines. Try again when it finishes." } } }))

  await page.goto('/#/drama/3/review')
  const row = await openDetails(page, 0)
  await row.getByRole('button', { name: 'Re-transcribe this line' }).click()
  await expect(row.getByTestId('retranscribe-line').getByRole('alert')).toBeVisible()
  await expect(row.getByRole('button', { name: 'Re-transcribe this line' })).toBeEnabled()
})
