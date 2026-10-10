import { type Page } from '@playwright/test'

import { expect, test } from './fixtures'
import { openTranscribeOptions, enableDeveloperMode } from './sourceHelpers'

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

// Advanced options are collapsed by default (and remembered once opened).
async function openAdvanced(page: Page) {
  await openTranscribeOptions(page)
  const details = page.locator('.section-title', { hasText: /^More options$/ }).locator('xpath=ancestor::details[1]')
  await expect(details).toBeVisible()
  if ((await details.getAttribute('open')) === null) await details.locator(':scope > summary').click()
  await expect(details).toHaveAttribute('open', '')
}

test('opens the workspace from the library and navigates stages', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Details: Signal' }).click()
  await page.getByRole('dialog', { name: 'Signal' }).getByRole('link', { name: 'Open workspace' }).click()
  // No stage in the link: the workspace opens the drama's current stage
  // (Source here -- the seeded drama has no lines yet).
  await expect(page).toHaveURL(/#\/drama\/3$/)
  await expect(page.getByTestId('drama-title')).toHaveText('Signal')
  // Header: humanized badges and a real back button.
  await expect(page.locator('.workspace-header .pill').first()).not.toHaveText(/_/)
  await expect(page.getByRole('link', { name: 'Back to Library' })).toHaveClass(/btn/)
  await expect(page.getByRole('link', { name: 'Source', exact: true })).toHaveAttribute('aria-current', 'page')
  await expect(page.getByTestId('media-status')).toContainText(/limit/i)

  await page.getByRole('navigation', { name: 'Stages' }).getByRole('link', { name: 'Review' }).click()
  await expect(page.getByRole('region', { name: 'Review' })).toBeVisible()

  await page.goto('/#/drama/3/not-a-stage')
  await expect(page.getByRole('link', { name: 'Source', exact: true })).toHaveAttribute('aria-current', 'page')
})

const progress = (stage: string, states: Record<string, string>) => ({
  drama_id: 1, stage_index: stage === 'review' ? 4 : 3, stage, line_count: 12, untranslated_count: 2,
  flagged_count: 1, has_audio: true, has_dub_track: false, exported: false,
  stages: Object.entries(states).map(([key, state]) => ({ key, state })),
})

test('opens on the reported stage and marks progress in the stepper (P16/P17)', async ({ page }) => {
  await page.route('**/api/workflow/dramas/1/progress', (route) =>
    route.fulfill({ json: progress('review', { source: 'done', translate: 'done', review: 'current', dub: 'optional', export: 'pending' }) }))
  await page.goto('/#/drama/1')
  const nav = page.getByRole('navigation', { name: 'Stages' })
  await expect(nav.getByRole('link', { name: 'Review', exact: true })).toHaveAttribute('aria-current', 'page')
  await expect(page.getByRole('region', { name: 'Review' })).toBeVisible()
  await expect(nav.getByRole('link', { name: 'Source', exact: true })).toHaveAttribute('data-state', 'done')
  await expect(nav.getByRole('link', { name: 'Review', exact: true })).toHaveAttribute('title', 'Review: Next step · 1 flagged')
  await expect(nav.getByRole('link', { name: 'Translate', exact: true })).toHaveAttribute('title', 'Translate: Done · 2 left')
  await expect(nav.getByRole('link', { name: 'Translate', exact: true })).toContainText('Translate· 2 left')
  await expect(nav.getByRole('link', { name: 'Dub', exact: true })).toHaveAttribute('data-state', 'optional')
  await expect(page.getByTestId('stage-counts')).toHaveText('12 lines')

  // A stage named in the URL wins over the reported one.
  await page.goto('/#/drama/1/export')
  await expect(nav.getByRole('link', { name: 'Export', exact: true })).toHaveAttribute('aria-current', 'page')
})

test('falls back to Source when progress cannot be read', async ({ page }) => {
  await page.route('**/api/workflow/dramas/2/progress', (route) =>
    route.fulfill({ status: 500, json: { error: { code: 'internal', message: 'boom' } } }))
  await page.goto('/#/drama/2')
  await expect(page.getByRole('link', { name: 'Source', exact: true })).toHaveAttribute('aria-current', 'page')
  await expect(page.getByTestId('stage-counts')).toHaveCount(0)
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

test('replacing existing audio needs the replace box, which is sent with the upload', async ({ page }) => {
  await page.route('**/api/media/dramas/1/status', (route) =>
    route.fulfill({ json: { drama_id: 1, has_audio: true, has_source_video: false, upload_max_mb: 500 } }),
  )
  const confirms: (string | null)[] = []
  await page.route('**/api/media/dramas/1/upload', async (route) => {
    const body = route.request().postDataBuffer()?.toString('latin1') ?? ''
    confirms.push(/name="confirm_replace_audio"\r\n\r\n(\w+)/.exec(body)?.[1] ?? null)
    await route.fulfill({ json: { name: 'source.mp3', size: 3, kind: 'audio', job_id: null } })
  })
  await page.goto('/#/drama/1/source')
  await expect(page.getByTestId('media-status')).toContainText('Audio attached')
  await page.getByLabel('Audio or video file').setInputFiles({ name: 'clip.mp3', mimeType: 'audio/mpeg', buffer: Buffer.from('abc') })
  const upload = page.getByRole('button', { name: 'Upload', exact: true })
  await expect(upload).toBeDisabled()

  await page.getByLabel(/Replace the current audio\/video/).check()
  await upload.click()
  await expect(page.getByRole('status').filter({ hasText: 'Uploaded audio' })).toBeVisible()
  expect(confirms).toEqual(['true'])
})

test('replacing a video with audio says the video is set aside, then shows no source video', async ({ page }) => {
  let video = true
  await page.route('**/api/media/dramas/1/status', (route) =>
    route.fulfill({ json: { drama_id: 1, has_audio: true, has_source_video: video, upload_max_mb: 500, kept_media_files: video ? 0 : 2, kept_media_bytes: video ? 0 : 2048 } }),
  )
  await page.route('**/api/media/dramas/1/upload', async (route) => {
    video = false
    await route.fulfill({ json: { name: 'source.mp3', size: 3, kind: 'audio', job_id: null } })
  })
  await page.goto('/#/drama/1/source')
  await expect(page.getByTestId('media-status')).toContainText('Source video attached')
  const input = page.getByLabel('Audio or video file')
  const note = page.getByTestId('replace-sets-video-aside')
  await expect(note).toContainText('sets the current video aside')

  await input.setInputFiles({ name: 'new.mp4', mimeType: 'video/mp4', buffer: Buffer.from('abc') })
  await expect(note).toHaveCount(0)
  await input.setInputFiles({ name: 'dub.mp3', mimeType: 'audio/mpeg', buffer: Buffer.from('abc') })
  await expect(note).toContainText('no source video for Review or video export')
  await expect(note).not.toContainText('burned-in')

  await page.getByLabel(/Replace the current audio\/video/).check()
  await page.getByRole('button', { name: 'Upload', exact: true }).click()
  await expect(page.getByTestId('media-status')).toContainText('No source video')
  await expect(page.getByTestId('kept-media')).toContainText('2 files')
  await expect(note).toHaveCount(0)
})

test('a hardsub title replaced with audio says the mode changes', async ({ page }) => {
  await page.route('**/api/media/dramas/1/status', (route) =>
    route.fulfill({
      json: { drama_id: 1, has_audio: true, has_source_video: true, reads_burned_in_subtitles: true, upload_max_mb: 500, kept_media_files: 0, kept_media_bytes: 0 },
    }),
  )
  await page.goto('/#/drama/1/source')
  await expect(page.getByTestId('media-status')).toContainText('Source video attached')
  await page.getByLabel('Audio or video file').setInputFiles({ name: 'dub.mp3', mimeType: 'audio/mpeg', buffer: Buffer.from('abc') })
  await expect(page.getByTestId('replace-sets-video-aside')).toContainText('from reading burned-in subtitles to transcribing the audio')
})

test('upload-and-transcribe over a video drops the stale video badge while the run is still going', async ({ page }) => {
  let video = true
  await page.route('**/api/media/dramas/1/status', (route) =>
    route.fulfill({ json: { drama_id: 1, has_audio: true, has_source_video: video, upload_max_mb: 500, kept_media_files: 0, kept_media_bytes: 0 } }),
  )
  await page.route('**/api/media/dramas/1/upload-and-transcribe', async (route) => {
    video = false
    await route.fulfill({ json: { upload: { name: 'source.mp3', size: 3, kind: 'audio', job_id: null }, job_id: 'fake-job' } })
  })
  await page.route('**/api/jobs/fake-job', (route) => route.fulfill({ json: job('running', { progress: 0.3 }) }))
  await page.goto('/#/drama/1/source')
  await expect(page.getByTestId('media-status')).toContainText('Source video attached')
  await page.getByLabel('Audio or video file').setInputFiles({ name: 'clip.mp3', mimeType: 'audio/mpeg', buffer: Buffer.from('abc') })
  // The transcript box only exists once the saved mode has loaded; count() below does not wait.
  await openTranscribeOptions(page)
  const transcript = page.getByLabel('Transcript text', { exact: true })
  if (await transcript.count()) await transcript.fill('line one')
  await page.getByLabel(/Replace the current audio\/video/).check()
  await page.getByRole('button', { name: 'Transcribe', exact: true }).click()
  await expect(page.getByTestId('job-status')).toContainText('Running')
  await expect(page.getByTestId('media-status')).toContainText('No source video')
  await expect(page.getByTestId('replace-sets-video-aside')).toHaveCount(0)
})

test('upload-and-transcribe waits for the replace box, also after the server asks for it', async ({ page }) => {
  // The status read says no media (e.g. another tab added it since): the server's 422 brings up the box.
  await page.route('**/api/media/dramas/1/status', (route) =>
    route.fulfill({
      json: { drama_id: 1, has_audio: false, has_source_video: false, upload_max_mb: 500, kept_media_files: 2, kept_media_bytes: 3_500_000 },
    }),
  )
  const confirms: (string | null)[] = []
  await page.route('**/api/media/dramas/1/upload-and-transcribe', async (route) => {
    const body = route.request().postDataBuffer()?.toString('latin1') ?? ''
    const confirm = /name="confirm_replace_audio"\r\n\r\n(\w+)/.exec(body)?.[1] ?? null
    confirms.push(confirm)
    if (!confirm) {
      return route.fulfill({
        status: 422,
        json: { error: { code: 'invalid_input', message: 'This drama already has audio. Confirm replacing it first.', details: { reason: 'confirm_replace_audio' } } },
      })
    }
    await route.fulfill({ json: { upload: { name: 'source.mp3', size: 3, kind: 'audio', job_id: null }, job_id: 'fake-job' } })
  })
  await page.route('**/api/jobs/fake-job', (route) => route.fulfill({ json: job('running') }))
  await page.goto('/#/drama/1/source')
  await expect(page.getByTestId('media-status')).toContainText('No audio')
  await page.getByLabel('Audio or video file').setInputFiles({ name: 'clip.mp3', mimeType: 'audio/mpeg', buffer: Buffer.from('abc') })
  const transcribe = page.getByRole('button', { name: 'Transcribe', exact: true })
  const transcript = page.getByLabel('Transcript text', { exact: true })
  if (await transcript.count()) await transcript.fill('line one')
  await transcribe.click()

  const box = page.getByLabel(/Replace the current audio\/video/)
  await expect(box).toBeVisible()
  // The staged file is not part of a run until Replace is ticked, so Transcribe is not held back for it.
  await expect(transcribe).toBeEnabled()
  await expect(page.getByTestId('transcribe-staged-unused')).toContainText('Replace the current audio/video')
  await expect(page.getByTestId('kept-media')).toContainText('2 files, 3.5 MB')
  await expect(page.getByTestId('kept-media')).toContainText('Disk usage')

  await box.check()
  await expect(transcribe).toBeEnabled()
  await transcribe.click()
  await expect(page.getByTestId('job-status')).toContainText('Running')
  expect(confirms).toEqual([null, 'true'])
})

test('after upload-and-transcribe the next run transcribes the stored audio instead of uploading again', async ({ page }) => {
  await page.route('**/api/media/dramas/1/status', (route) =>
    route.fulfill({ json: { drama_id: 1, has_audio: true, has_source_video: false, upload_max_mb: 500, kept_media_files: 0, kept_media_bytes: 0 } }),
  )
  const uploads: (string | null)[] = []
  await page.route('**/api/media/dramas/1/upload-and-transcribe', async (route) => {
    const body = route.request().postDataBuffer()?.toString('latin1') ?? ''
    uploads.push(/name="confirm_replace_audio"\r\n\r\n(\w+)/.exec(body)?.[1] ?? null)
    await route.fulfill({ json: { upload: { name: 'source-2.mp3', size: 3, kind: 'audio', job_id: null }, job_id: 'fake-job' } })
  })
  const runs: unknown[] = []
  await page.route('**/api/transcribe/dramas/1/run', async (route) => {
    runs.push(route.request().postDataJSON())
    await route.fulfill({ json: { job_id: 'fake-job' } })
  })
  await page.route('**/api/jobs/fake-job', (route) => route.fulfill({ json: job('done', { progress: 1, finished_at: 2 }) }))
  await page.goto('/#/drama/1/source')
  await expect(page.getByTestId('media-status')).toContainText('Audio attached')
  await page.getByLabel('Audio or video file').setInputFiles({ name: 'clip.mp3', mimeType: 'audio/mpeg', buffer: Buffer.from('abc') })
  // The transcript box only exists once the saved mode has loaded; count() below does not wait.
  await openTranscribeOptions(page)
  const transcript = page.getByLabel('Transcript text', { exact: true })
  if (await transcript.count()) await transcript.fill('line one')
  const box = page.getByLabel(/Replace the current audio\/video/)
  await box.check()
  const transcribe = page.getByRole('button', { name: 'Transcribe', exact: true })
  await transcribe.click()
  await expect(page.getByTestId('job-status')).toContainText('Done')
  expect(uploads).toEqual(['true'])

  // The picked file and the replace tick are spent: the next run uses the stored audio.
  await expect(box).not.toBeChecked()
  await expect(page.getByRole('button', { name: 'Upload', exact: true })).toBeDisabled()
  await expect(transcribe).toBeEnabled()
  await transcribe.click()
  await expect.poll(() => runs.length).toBe(1)
  expect(uploads).toEqual(['true'])
})

test('starts a transcription with the right body, polls the job and cancels it', async ({ page }) => {
  await enableDeveloperMode(page)
  const run = await mockRun(page, 1)
  await page.goto('/#/drama/1/source')
  await expect(page.getByRole('region', { name: 'Transcribe' })).toBeVisible()
  // The config form (and the Transcript text box, which depends on it) renders only once the config has loaded.
  await openAdvanced(page)
  await expect(page.getByLabel('Beam size', { exact: true })).toBeVisible()
  await page.getByLabel('Extra names to expect', { exact: true }).fill('names: Wei')
  await page.getByLabel('Expected speakers', { exact: true }).fill('2')
  const transcript = page.getByLabel('Transcript text', { exact: true })
  if (await transcript.count()) await transcript.fill('line one')

  await page.getByRole('button', { name: 'Transcribe', exact: true }).click()
  await expect(page.getByTestId('job-status')).toContainText('Running')
  expect(run.bodies[0]).toMatchObject({ extra_names: 'names: Wei', expected_speakers: 2, run_diarize: false })

  await page.getByRole('button', { name: 'Cancel job' }).click()
  await expect(page.getByTestId('job-status')).toContainText('Cancelled')
  await expect(page.getByRole('button', { name: 'Cancel job' })).toHaveCount(0)
})

test('an out-of-range option is caught before saving and a server 409 shows a banner', async ({ page }) => {
  await enableDeveloperMode(page)
  await page.route('**/api/transcribe/dramas/1/run', (route) =>
    route.fulfill({ status: 409, json: { error: { code: 'conflict', message: 'A job is already running.' } } }))
  await page.goto('/#/drama/1/source')
  // The config form (and the Transcript text box, which depends on it) renders only once the config has loaded.
  await openAdvanced(page)
  await expect(page.getByLabel('Beam size', { exact: true })).toBeVisible()
  await page.getByLabel('Beam size', { exact: true }).fill('11')
  await page.getByRole('button', { name: 'Save options' }).click()
  await expect(page.getByRole('alert')).toContainText('Beam size')
  // Running with the bad value is caught too, and nothing is sent.
  const draft = page.getByLabel('Transcript text', { exact: true })
  if (await draft.count()) await draft.fill('line one')
  await page.getByRole('button', { name: 'Transcribe', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('Beam size')
  await page.getByLabel('Beam size', { exact: true }).fill('5')

  const transcript = page.getByLabel('Transcript text', { exact: true })
  if (await transcript.count()) await transcript.fill('line one')
  await page.getByRole('button', { name: 'Transcribe', exact: true }).click()
  await expect(page.getByRole('alert').filter({ hasText: 'cannot be done right now' })).toBeVisible()
})

test('switching dramas does not leak stage state', async ({ page }) => {
  await enableDeveloperMode(page)
  await mockRun(page, 1)
  await page.goto('/#/drama/1/source')
  // The config form (and the Transcript text box, which depends on it) renders only once the config has loaded.
  await openAdvanced(page)
  await expect(page.getByLabel('Beam size', { exact: true })).toBeVisible()
  await page.getByLabel('Extra names to expect', { exact: true }).fill('leaky prompt')
  const transcript = page.getByLabel('Transcript text', { exact: true })
  if (await transcript.count()) await transcript.fill('line one')
  await page.getByRole('button', { name: 'Transcribe', exact: true }).click()
  await expect(page.getByTestId('job-panel')).toBeVisible()
  const firstTitle = await page.getByTestId('drama-title').innerText()

  await page.goto('/#/drama/2/source')
  await expect(page.getByTestId('drama-title')).not.toHaveText(firstTitle)
  await expect(page.getByLabel('Extra names to expect', { exact: true })).toHaveValue('')
  await expect(page.getByTestId('job-panel')).toHaveCount(0)
})

test('source options offer turbo with a Korean/Chinese note, the Taiwan script label and a GPU note', async ({ page }) => {
  await enableDeveloperMode(page)
  await page.goto('/#/drama/1/source')
  await openAdvanced(page)
  await expect(page.getByLabel('Beam size', { exact: true })).toBeVisible()
  const size = page.getByLabel('Whisper model', { exact: true })
  await expect(size.locator('option[value="large-v3-turbo"]')).toHaveCount(1)
  await expect(size.locator('option[value="large-v3-turbo"]')).toHaveText('large-v3-turbo (default)')
  // The Edit details panel has its own "Source language" select; scope to Transcribe.
  const language = page.getByRole('region', { name: 'Transcribe' }).getByLabel('Source language', { exact: true })
  await size.selectOption('large-v3-turbo')
  await language.selectOption('ko')
  await expect(page.getByRole('note').filter({ hasText: 'half a point' })).toContainText('about twice as slow')
  await language.selectOption('ja')
  await expect(page.getByRole('note').filter({ hasText: 'our tests' })).toHaveCount(0)
  await language.selectOption('zh')
  await expect(page.getByRole('note').filter({ hasText: 'tests disagree' })).toHaveCount(1)
  await expect(page.getByLabel('Chinese script', { exact: true }).locator('option', { hasText: 'Traditional (Taiwan, Hong Kong)' })).toHaveCount(1)
  await expect(page.getByTestId('gpu-note')).toContainText(/GPU: (on|off) - change in Settings/)
})

test('form state and the running job survive a stage-tab switch', async ({ page }) => {
  await enableDeveloperMode(page)
  await mockRun(page, 1)
  await page.route('**/api/jobs/transcribe_1', (route) => route.fulfill({ json: job('running', { job_id: 'transcribe_1' }) }))
  await page.goto('/#/drama/1/source')
  await openAdvanced(page)
  await expect(page.getByLabel('Beam size', { exact: true })).toBeVisible()
  await page.getByLabel('Extra names to expect', { exact: true }).fill('keep me')
  await page.getByRole('navigation', { name: 'Stages' }).getByRole('link', { name: 'Review' }).click()
  await expect(page.getByRole('region', { name: 'Review' })).toBeVisible()
  await page.getByRole('navigation', { name: 'Stages' }).getByRole('link', { name: 'Source', exact: true }).click()
  await expect(page.getByLabel('Extra names to expect', { exact: true })).toHaveValue('keep me')
  await expect(page.getByTestId('job-status')).toContainText('Running')
  await expect(page.getByTestId('job-percent')).toHaveText('40%')
  await expect(page.getByRole('button', { name: 'Transcribe', exact: true })).toBeDisabled()
  await expect(page.getByText(/already running/)).toBeVisible()
})

test('a second run with the same job id shows the new run, not the stale done', async ({ page }) => {
  await enableDeveloperMode(page)
  let runs = 0
  await page.route('**/api/transcribe/dramas/1/run', (route) => { runs += 1; return route.fulfill({ json: { job_id: 'transcribe_1' } }) })
  await page.route('**/api/jobs/transcribe_1', (route) => {
    // Run one has finished; run two (same job id) is in progress.
    return route.fulfill({ json: job(runs >= 2 ? 'running' : 'done', { job_id: 'transcribe_1', message: runs >= 2 ? 'second' : 'first' }) })
  })
  await page.goto('/#/drama/1/source')
  await openAdvanced(page)
  await expect(page.getByLabel('Beam size', { exact: true })).toBeVisible()
  const transcript = page.getByLabel('Transcript text', { exact: true })
  if (await transcript.count()) await transcript.fill('line one')
  await page.getByRole('button', { name: 'Transcribe', exact: true }).click()
  await expect(page.getByTestId('job-status')).toContainText('Done')
  await expect(page.getByRole('button', { name: 'Transcribe', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: 'Transcribe', exact: true }).click()
  await expect(page.getByTestId('job-status')).toContainText('second')
  await expect(page.getByRole('button', { name: 'Transcribe', exact: true })).toBeDisabled()
})

test('the primary action is Transcribe, options are collapsed and changed options auto-save on run', async ({ page }) => {
  await enableDeveloperMode(page)
  const run = await mockRun(page, 1)
  const saves: Record<string, unknown>[] = []
  await page.route('**/api/transcribe/dramas/1/config', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    const body = route.request().postDataJSON() as Record<string, unknown>
    saves.push(body)
    const current = await (await route.fetch({ method: 'GET' })).json()
    return route.fulfill({ json: { ...current, ...body } })
  })
  await page.goto('/#/drama/1/source')
  const region = page.getByRole('region', { name: 'Transcribe' })
  await expect(region.locator('button.primary')).toHaveText('Transcribe')
  await expect(page.getByTestId('settings-summary')).toContainText('Chinese')
  // Collapsed: the tuning fields are not visible until Advanced is opened.
  await expect(page.getByLabel('Beam size', { exact: true })).toBeHidden()
  await openTranscribeOptions(page)
  await expect(region.locator('details.section > summary').filter({ hasText: 'More options' }).first()).toContainText(/defaults/i)
  await openAdvanced(page)
  await page.getByLabel('Beam size', { exact: true }).fill('7')
  const transcript = page.getByLabel('Transcript text', { exact: true })
  if (await transcript.count()) await transcript.fill('line one')

  await page.getByRole('button', { name: 'Transcribe', exact: true }).click()
  await expect(page.getByTestId('job-status')).toContainText('Running')
  expect(saves).toHaveLength(1)
  expect(saves[0]).toMatchObject({ beam_size: 7 })
  expect(run.bodies).toHaveLength(1)
})
