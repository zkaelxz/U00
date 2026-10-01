import { expect, test, type Page } from '@playwright/test'

// Real seeded API for the reads; config and run endpoints are mocked where a
// speakable drama or a run is needed, so no TTS ever runs.

const dubConfig = (over: object = {}) => ({
  drama_id: 1, content_mode: null, is_narration: false, narration_language: 'en',
  narration_language_options: ['en', 'zh'], source_language: 'zh',
  tts_engines: [{ key: 'edge_tts', label: 'Edge TTS', requires_internet: true }],
  defaults: { max_speedup: 1.3, max_slowdown: 0.85, speedup_range: [1, 2], slowdown_range: [0.5, 1] },
  speakers: [], gpu_required: false, speakable_line_count: 3, track_available: false,
  gpt_sovits_configured: false, can_keep_background: true, ...over,
})

const job = (status: string) => ({
  job_id: 'fake-dub', status, progress: 0.5, message: 'working', error: null, description: null,
  gpu_touching: false, started_at: 1, finished_at: null, updated_at: 1,
})

async function mockConfig(page: Page, over: object = {}) {
  await page.route('**/api/dub/dramas/1/config', (route) => route.fulfill({ json: dubConfig(over) }))
  await page.route('**/api/dub/dramas/1/pacing', (route) =>
    route.fulfill({
      json: {
        available: true, counts: { fit: 1, overflow: 1 },
        lines: [{ idx: 2, status: 'overflow', factor: 1.5, clip_ms: null, window_ms: 800 }],
      },
    }))
}

test('shows config and null-safe pacing, then starts a dub with the right body and cancels', async ({ page }) => {
  await mockConfig(page)
  const bodies: unknown[] = []
  let cancelled = false
  await page.route('**/api/dub/dramas/1/run', async (route) => {
    bodies.push(route.request().postDataJSON())
    await route.fulfill({ json: { job_id: 'fake-dub' } })
  })
  await page.route('**/api/jobs/fake-dub', (route) => route.fulfill({ json: cancelled ? job('cancelled') : job('running') }))
  await page.route('**/api/jobs/fake-dub/cancel', async (route) => {
    cancelled = true
    await route.fulfill({ json: { job_id: 'fake-dub', cancel_requested: true, status: 'cancelled' } })
  })

  await page.goto('/#/drama/1/dub')
  await expect(page.getByTestId('dub-summary')).toContainText('3 speakable lines')
  await expect(page.getByText('Downloading the finished dub track')).toHaveCount(0)
  const pacing = page.locator('details.section', { hasText: 'Pacing of the last run' })
  await expect(pacing).toContainText('1 overflow')
  await pacing.locator('summary').click()
  await expect(page.getByRole('cell', { name: 'n/a' })).toBeVisible()
  await page.screenshot({ path: 'e2e/screenshots-tmp/dub-after.png', fullPage: true })

  await page.getByText('Advanced', { exact: true }).click()
  await page.getByRole('spinbutton', { name: 'Max speed-up' }).fill('1.5')
  await page.getByRole('switch', { name: 'Keep background music' }).click()
  await page.getByRole('button', { name: 'Generate dub' }).click()
  await expect(page.getByTestId('job-status')).toContainText('running')
  expect(bodies[0]).toEqual({ tts_engine: 'edge_tts', max_speedup: 1.5, max_slowdown: 0.85, keep_background: true })

  await page.getByRole('button', { name: 'Cancel job' }).click()
  await expect(page.getByTestId('job-status')).toContainText('cancelled')
})

test('keep-background is disabled when unavailable, and 503 shows a plain banner', async ({ page }) => {
  await mockConfig(page, { can_keep_background: false })
  await page.route('**/api/dub/dramas/1/run', (route) =>
    route.fulfill({ status: 503, json: { error: { code: 'dependency_unavailable', message: 'ffmpeg is not installed.' } } }))
  await page.goto('/#/drama/1/dub')
  await page.getByText('Advanced', { exact: true }).click()
  await expect(page.getByRole('switch', { name: 'Keep background music' })).toBeDisabled()
  await expect(page.getByTestId('dub-bgm-reason')).toContainText('separation tools')
  await page.getByRole('button', { name: 'Generate dub' }).click()
  await expect(page.getByRole('alert')).toContainText('not installed or not reachable')
})

test('nothing speakable blocks the run button on the seeded drama', async ({ page }) => {
  await page.goto('/#/drama/1/dub')
  await expect(page.getByTestId('dub-summary')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Generate dub' })).toBeDisabled()
  // Rule 22: the reason links to where the text comes from.
  const reason = page.getByTestId('dub-settings')
  await expect(reason).toContainText('There is no text to speak yet.')
  await reason.getByRole('link', { name: 'Go to Source' }).click()
  await expect(page).toHaveURL(/#\/drama\/1\/source$/)
})

test('narration chunk-and-tag needs a confirmation before replacing lines', async ({ page }) => {
  await mockConfig(page, { is_narration: true, defaults: null })
  await page.route('**/api/narration/dramas/1/config', (route) =>
    route.fulfill({
      json: {
        drama_id: 1, is_narration: true, has_novel_source: true,
        engines: [{ key: 'fake', key_configured: true }], default_engine: 'fake',
        max_chunk_chars: 500, existing_line_count: 12, replaces_existing_lines: true, job_running: false,
      },
    }))
  const bodies: unknown[] = []
  await page.route('**/api/narration/dramas/1/run', async (route) => {
    bodies.push(route.request().postDataJSON())
    await route.fulfill({ status: 409, json: { error: { code: 'conflict', message: 'Already running.' } } })
  })
  await page.goto('/#/drama/1/dub')
  await page.getByText('Chunk and tag speakers', { exact: true }).click()
  const start = page.getByRole('button', { name: 'Chunk and tag' })
  await expect(start).toBeDisabled()
  await page.getByLabel('Replace the 12 existing lines').check()
  await start.click()
  await expect(page.getByRole('alert').filter({ hasText: 'cannot be done right now' })).toBeVisible()
  expect(bodies[0]).toEqual({ engine: 'fake' })
})

test('narration Start over sends ?fresh=true only while it is on, and a resumed run says so', async ({ page }) => {
  await mockConfig(page, { is_narration: true, defaults: null })
  await page.route('**/api/narration/dramas/1/config', (route) =>
    route.fulfill({
      json: {
        drama_id: 1, is_narration: true, has_novel_source: true,
        engines: [{ key: 'fake', key_configured: true }], default_engine: 'fake',
        max_chunk_chars: 500, existing_line_count: 0, replaces_existing_lines: false, job_running: false,
      },
    }))
  const posts: { url: string; body: unknown }[] = []
  // A regex, so the start is matched with or without its query string.
  await page.route(/\/api\/narration\/dramas\/1\/run(\?.*)?$/, async (route) => {
    posts.push({ url: route.request().url(), body: route.request().postDataJSON() })
    await route.fulfill({ json: { job_id: `fake-narration-${posts.length}` } })
  })
  let resumed = true
  await page.route('**/api/jobs/fake-narration-*', (route) =>
    route.fulfill({
      json: {
        ...job('done'), job_id: 'fake-narration', progress: 1,
        message: resumed ? 'Resuming: 3 of 8 chunks already tagged...' : 'Tagged 8 chunks',
      },
    }))
  await page.goto('/#/drama/1/dub')
  await page.getByText('Chunk and tag speakers', { exact: true }).click()
  const startOver = page.getByRole('switch', { name: 'Start over' })
  const start = page.getByRole('button', { name: 'Chunk and tag' })
  await expect(startOver).toHaveAttribute('aria-checked', 'false')

  await start.click()
  await expect.poll(() => posts.length).toBe(1)
  expect(new URL(posts[0].url).search).toBe('')
  expect(posts[0].body).toEqual({ engine: 'fake' })
  await expect(page.getByTestId('job-status')).toContainText('Resuming: 3 of 8')
  await expect(page.getByTestId('job-note')).toHaveText(
    'Resuming an interrupted run. Use Start over to tag everything again.',
  )

  resumed = false
  await startOver.click()
  await expect(startOver).toHaveAttribute('aria-checked', 'true')
  await expect(start).toBeEnabled()
  await start.click()
  await expect.poll(() => posts.length).toBe(2)
  expect(new URL(posts[1].url).search).toBe('?fresh=true')
  expect(posts[1].body).toEqual({ engine: 'fake' })
  await expect(page.getByTestId('job-status')).toContainText('Tagged 8 chunks')
  await expect(page.getByTestId('job-note')).toHaveCount(0)
})
