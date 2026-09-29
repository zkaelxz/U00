import { expect, test, type Page } from '@playwright/test'

// Offline paths only: the run/job endpoints are mocked, so nothing is
// transcribed. Reads and the upload pre-check hit the real seeded API.

const job = (status: string, extra: object = {}) => ({
  job_id: 'fake-job', status, progress: 0.4, message: 'working', error: null, description: null,
  gpu_touching: false, started_at: 1, finished_at: null, updated_at: 1, ...extra,
})

async function mockRun(page: Page, dramaId: number) {
  const bodies: unknown[] = []
  let cancelled = false
  await page.route(`**/api/transcribe/dramas/${dramaId}/run`, async (route) => {
    bodies.push(route.request().postDataJSON())
    await route.fulfill({ json: { job_id: 'fake-job' } })
  })
  await page.route('**/api/jobs/fake-job', async (route) => {
    // Stay "running" until cancelled, then finish.
    await route.fulfill({ json: cancelled ? job('cancelled', { finished_at: 2 }) : job('running') })
  })
  await page.route('**/api/jobs/fake-job/cancel', async (route) => {
    cancelled = true
    await route.fulfill({ json: { job_id: 'fake-job', cancel_requested: true, status: 'cancelled' } })
  })
  return { bodies }
}

test('opens the workspace from the library and navigates stages', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('row').filter({ hasText: 'Signal' }).click()
  await page.getByRole('link', { name: 'Open workspace' }).click()
  await expect(page).toHaveURL(/#\/drama\/3\/source$/)
  await expect(page.getByTestId('drama-title')).toHaveText('Signal')
  await expect(page.getByTestId('media-status')).toContainText('Upload limit')

  await page.getByRole('navigation', { name: 'Stages' }).getByRole('link', { name: 'Review' }).click()
  await expect(page.getByRole('region', { name: 'Review' })).toBeVisible()

  await page.goto('/#/drama/3/not-a-stage')
  await expect(page.getByRole('link', { name: 'Source', exact: true })).toHaveAttribute('aria-current', 'page')
})

test('rejects an unsupported upload before sending it', async ({ page }) => {
  const uploads: string[] = []
  page.on('request', (r) => r.url().includes('/upload') && uploads.push(r.url()))
  await page.goto('/#/drama/1/source')
  await expect(page.getByTestId('media-status')).toBeVisible()
  const input = page.getByLabel('Audio or video file')

  await input.setInputFiles({ name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('hi') })
  await expect(page.getByRole('alert')).toContainText('not supported')
  await expect(page.getByRole('button', { name: 'Upload', exact: true })).toBeDisabled()

  await input.setInputFiles({ name: 'clip.mp3', mimeType: 'audio/mpeg', buffer: Buffer.from('abc') })
  await expect(page.getByRole('button', { name: 'Upload', exact: true })).toBeEnabled()
  expect(uploads).toEqual([])
})

test('starts a transcription with the right body, polls the job and cancels it', async ({ page }) => {
  const run = await mockRun(page, 1)
  await page.goto('/#/drama/1/source')
  await expect(page.getByRole('region', { name: 'Transcribe' })).toBeVisible()
  // The config form (and the Transcript text box, which depends on it) renders only once the config has loaded.
  await expect(page.getByLabel('Beam size (1-10)')).toBeVisible()
  await page.getByLabel('Initial prompt').fill('names: Wei')
  await page.getByLabel('Expected speakers (0-20, blank = auto)').fill('2')
  const transcript = page.getByLabel('Transcript text')
  if (await transcript.count()) await transcript.fill('line one')

  await page.getByRole('button', { name: 'Start transcription' }).click()
  await expect(page.getByTestId('job-status')).toContainText('running')
  expect(run.bodies[0]).toMatchObject({ initial_prompt: 'names: Wei', expected_speakers: 2, run_diarize: false })

  await page.getByRole('button', { name: 'Cancel job' }).click()
  await expect(page.getByTestId('job-status')).toContainText('cancelled')
  await expect(page.getByRole('button', { name: 'Cancel job' })).toHaveCount(0)
})

test('an out-of-range option is caught before saving and a server 409 shows a banner', async ({ page }) => {
  await page.route('**/api/transcribe/dramas/1/run', (route) =>
    route.fulfill({ status: 409, json: { error: { code: 'conflict', message: 'A job is already running.' } } }))
  await page.goto('/#/drama/1/source')
  // The config form (and the Transcript text box, which depends on it) renders only once the config has loaded.
  await expect(page.getByLabel('Beam size (1-10)')).toBeVisible()
  await page.getByLabel('Beam size (1-10)').fill('11')
  await page.getByRole('button', { name: 'Save options' }).click()
  await expect(page.getByRole('alert')).toContainText('beam size')

  const transcript = page.getByLabel('Transcript text')
  if (await transcript.count()) await transcript.fill('line one')
  await page.getByRole('button', { name: 'Start transcription' }).click()
  await expect(page.getByRole('alert').filter({ hasText: 'cannot be done right now' })).toBeVisible()
})

test('switching dramas does not leak stage state', async ({ page }) => {
  await mockRun(page, 1)
  await page.goto('/#/drama/1/source')
  // The config form (and the Transcript text box, which depends on it) renders only once the config has loaded.
  await expect(page.getByLabel('Beam size (1-10)')).toBeVisible()
  await page.getByLabel('Initial prompt').fill('leaky prompt')
  const transcript = page.getByLabel('Transcript text')
  if (await transcript.count()) await transcript.fill('line one')
  await page.getByRole('button', { name: 'Start transcription' }).click()
  await expect(page.getByTestId('job-panel')).toBeVisible()
  const firstTitle = await page.getByTestId('drama-title').innerText()

  await page.goto('/#/drama/2/source')
  await expect(page.getByTestId('drama-title')).not.toHaveText(firstTitle)
  await expect(page.getByLabel('Initial prompt')).toHaveValue('')
  await expect(page.getByTestId('job-panel')).toHaveCount(0)
})

test('source options offer turbo with a ja/ko hint, the Taiwan script label and a GPU note', async ({ page }) => {
  await page.goto('/#/drama/1/source')
  await expect(page.getByLabel('Beam size (1-10)')).toBeVisible()
  const size = page.getByLabel('Whisper size')
  await expect(size.locator('option[value="large-v3-turbo"]')).toHaveCount(1)
  await page.getByLabel('Source language').selectOption('ja')
  await size.selectOption('large-v3-turbo')
  await expect(page.getByRole('note')).toContainText('weaker on Japanese and Korean')
  await page.getByLabel('Source language').selectOption('zh')
  await expect(page.getByRole('note')).toHaveCount(0)
  await expect(page.getByLabel('Chinese script').locator('option', { hasText: 'Traditional (Taiwan, Hong Kong)' })).toHaveCount(1)
  await expect(page.getByTestId('gpu-note')).toContainText(/GPU: (on|off) - change in Settings/)
})

test('form state and the running job survive a stage-tab switch', async ({ page }) => {
  await mockRun(page, 1)
  await page.route('**/api/jobs/transcribe_1', (route) => route.fulfill({ json: job('running', { job_id: 'transcribe_1' }) }))
  await page.goto('/#/drama/1/source')
  await expect(page.getByLabel('Beam size (1-10)')).toBeVisible()
  await page.getByLabel('Initial prompt').fill('keep me')
  await page.getByRole('navigation', { name: 'Stages' }).getByRole('link', { name: 'Review' }).click()
  await expect(page.getByRole('region', { name: 'Review' })).toBeVisible()
  await page.getByRole('navigation', { name: 'Stages' }).getByRole('link', { name: 'Source', exact: true }).click()
  await expect(page.getByLabel('Initial prompt')).toHaveValue('keep me')
  await expect(page.getByTestId('job-status')).toContainText('running')
  await expect(page.getByTestId('job-percent')).toHaveText('40%')
  await expect(page.getByRole('button', { name: 'Start transcription' })).toBeDisabled()
  await expect(page.getByText(/already running/)).toBeVisible()
})
