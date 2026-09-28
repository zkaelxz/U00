import { expect, test, type Page } from '@playwright/test'

// Reads, the ASS/subtitle text and the flag actions hit the real seeded API
// (the seeded dramas have no lines, so counts are zero and flags write
// nothing). Job starts are mocked: no ffmpeg runs.

const job = (status: string, extra: object = {}) => ({
  job_id: 'fake-export', status, progress: 0.5, message: 'encoding', error: null, description: null,
  gpu_touching: false, started_at: 1, finished_at: null, updated_at: 1, ...extra,
})

async function mockJob(page: Page, startPath: string, finalStatus: 'done' | 'cancelled') {
  const bodies: unknown[] = []
  let finished = false
  await page.route(`**${startPath}`, async (route) => {
    bodies.push(route.request().postDataJSON())
    await route.fulfill({ json: { job_id: 'fake-export' } })
  })
  await page.route('**/api/jobs/fake-export', async (route) => {
    await route.fulfill({ json: finished ? job(finalStatus, { finished_at: 2 }) : job('running') })
  })
  await page.route('**/api/jobs/fake-export/cancel', async (route) => {
    finished = true
    await route.fulfill({ json: { job_id: 'fake-export', cancel_requested: true, status: 'cancelled' } })
  })
  return { bodies, finish: () => (finished = true) }
}

test('shows readiness and generates subtitle and ASS text', async ({ page }) => {
  await page.goto('/#/drama/1/export')
  await expect(page.getByTestId('readiness')).toContainText('Lines: 0')

  const srt = page.waitForResponse((r) => r.url().includes('/subtitle?') && r.status() === 200)
  await page.getByRole('button', { name: 'Generate SRT' }).click()
  await srt
  await expect(page.getByTestId('export-empty')).toBeVisible()

  await page.getByRole('button', { name: 'Generate ASS' }).click()
  await expect(page.getByTestId('export-text')).toContainText('[Script Info]')
  await expect(page.getByTestId('export-download')).toHaveAttribute('download', 'drama_1_en.ass')
})

test('bad ASS settings are explained before any request', async ({ page }) => {
  await page.goto('/#/drama/1/export')
  await page.getByLabel(/^Text colour/).fill('red')
  await page.getByRole('button', { name: 'Generate ASS' }).click()
  await expect(page.getByRole('alert').filter({ hasText: '#RRGGBB' })).toBeVisible()
})

test('flag actions run only on click and report the result', async ({ page }) => {
  let posts = 0
  page.on('request', (r) => r.method() === 'POST' && r.url().includes('/flag-') && posts++)
  await page.goto('/#/drama/1/export')
  await expect(page.getByTestId('readiness')).toBeVisible()
  expect(posts).toBe(0)
  await page.getByRole('button', { name: 'Flag overlapping lines' }).click()
  await expect(page.getByTestId('flag-result-overlaps')).toHaveText('Flagged 0 lines.')
  await page.getByRole('button', { name: 'Run auto-QC and flag' }).click()
  await expect(page.getByTestId('flag-result-qc')).toContainText('Checked 0 lines')
  expect(posts).toBe(2)
})

test('a drama that is not novel narration has no EPUB section', async ({ page }) => {
  await page.goto('/#/drama/1/export')
  await expect(page.getByTestId('readiness')).toBeVisible()
  await expect(page.getByRole('region', { name: 'EPUB' })).toHaveCount(0)
})

test('audiobook job can be cancelled', async ({ page }) => {
  await mockJob(page, '/api/export/dramas/1/audiobook', 'cancelled')
  await page.goto('/#/drama/1/export')
  await page.getByRole('button', { name: 'Start audiobook export' }).click()
  await expect(page.getByTestId('job-status')).toContainText('running')
  await page.getByRole('button', { name: 'Cancel job' }).click()
  await expect(page.getByTestId('job-status')).toContainText('cancelled')
})

test('burned-in video sends the style and offers the artifact on done', async ({ page }) => {
  const { bodies, finish } = await mockJob(page, '/api/export/dramas/1/burned-video', 'done')
  await page.route('**/api/artifacts/dramas/1/video/info', (route) =>
    route.fulfill({ json: { name: 'burned_video_1.mp4', size: 3 * 1024 * 1024, kind: 'video' } }),
  )
  await page.goto('/#/drama/1/export')
  await page.getByLabel(/^Font size/).fill('48')
  await page.getByRole('button', { name: 'Start burned-in video export' }).click()
  await expect(page.getByTestId('job-status')).toContainText('running')
  finish()
  const link = page.getByTestId('artifact-video').getByRole('link')
  await expect(link).toHaveText('Download burned_video_1.mp4')
  await expect(link).toHaveAttribute('href', /\/api\/artifacts\/dramas\/1\/video$/)
  expect(bodies[0]).toMatchObject({ field: 'en', style: { size: 48 } })
})

test('a 422 from a job start is shown as a banner', async ({ page }) => {
  await page.route('**/api/export/dramas/1/audiobook', (route) =>
    route.fulfill({ status: 422, json: { error: { code: 'invalid_input', message: 'No narration audio yet.' } } }),
  )
  await page.goto('/#/drama/1/export')
  await page.getByRole('button', { name: 'Start audiobook export' }).click()
  await expect(page.getByRole('alert').filter({ hasText: 'not valid' })).toBeVisible()
})
