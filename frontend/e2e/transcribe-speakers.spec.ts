import { expect, test, type Page } from '@playwright/test'
import { openTranscribeOptions } from './sourceHelpers'

// Parity D03 (Expected speakers defaults to the last run), D06 (ask before
// replacing hand-corrected speakers) and D04 (time estimates). The
// diarization config, media status, media analysis and the run are mocked;
// other reads hit the seeded API.

async function mockSpeakers(page: Page, config: Record<string, unknown>) {
  await page.route('**/api/diarization/dramas/1/config', (r) =>
    r.fulfill({
      json: {
        drama_id: 1, hf_token_configured: true, expected_speakers: null, min_speakers: null,
        max_speakers: null, last_device: null, audio_available: true, manual_speaker_count: 0, ...config,
      },
    }),
  )
  await page.route('**/api/media/dramas/1/status', (r) =>
    r.fulfill({ json: { drama_id: 1, has_audio: true, has_source_video: false, upload_max_mb: 500 } }),
  )
  await page.route('**/api/metadata/dramas/1/analyze-media', (r) =>
    r.fulfill({ json: { drama_id: 1, duration_seconds: 600, has_video: false, has_audio: true, audio_track_count: 1, sample_rate: 44100 } }),
  )
  const urls: string[] = []
  await page.route('**/api/diarization/dramas/1/run**', (r) => {
    urls.push(r.request().url())
    return r.fulfill({ json: { job_id: 'fake-diarize' } })
  })
  await page.route('**/api/jobs/fake-diarize', (r) =>
    r.fulfill({
      json: {
        job_id: 'fake-diarize', status: 'running', progress: 0, message: 'working', error: null,
        description: null, gpu_touching: true, started_at: 1, finished_at: null, updated_at: 1,
      },
    }),
  )
  await page.route('**/api/jobs/fake-diarize/cancel', (r) => r.abort())
  return urls
}

async function openSpeakers(page: Page) {
  await page.goto('/#/drama/1/source')
  await expect(page.getByRole('region', { name: 'Transcribe' })).toBeVisible()
  await openTranscribeOptions(page)
  await page.locator('.section-title', { hasText: /^More options$/ }).click()
}

test('Expected speakers starts at the last run\'s count; corrections are kept by default (D03, D06)', async ({ page }) => {
  const urls = await mockSpeakers(page, { expected_speakers: 3, manual_speaker_count: 2 })
  await openSpeakers(page)
  await expect(page.getByLabel('Expected speakers', { exact: true })).toHaveValue('3')
  await expect(page.getByTestId('manual-speakers')).toContainText('Your 2 speaker corrections are kept.')
  await page.getByRole('button', { name: 'Detect speakers only' }).click()
  await expect.poll(() => urls.length).toBe(1)
  const sent = new URL(urls[0]).searchParams
  expect(sent.get('expected_speakers')).toBe('3')
  expect(sent.get('overwrite_manual')).toBeNull()
  expect(sent.get('confirm')).toBeNull()
})

test('replacing corrections needs the acknowledgement and sends overwrite_manual with confirm (D06)', async ({ page }) => {
  const urls = await mockSpeakers(page, { manual_speaker_count: 1 })
  await openSpeakers(page)
  await expect(page.getByLabel('Expected speakers', { exact: true })).toHaveValue('')
  await page.getByRole('switch', { name: 'Replace my 1 speaker correction' }).click()
  const detect = page.getByRole('button', { name: 'Detect speakers only' })
  await expect(detect).toBeDisabled()
  await expect(page.getByText('Still needed: tick the confirmation above, or turn Replace off.')).toBeVisible()
  await page.getByLabel('I understand my 1 speaker correction will be replaced').check()
  await detect.click()
  await expect.poll(() => urls.length).toBe(1)
  const sent = new URL(urls[0]).searchParams
  expect(sent.get('overwrite_manual')).toBe('true')
  expect(sent.get('confirm')).toBe('true')
})

test('no corrections: no replace switch; the time estimates show next to both buttons (D04)', async ({ page }) => {
  await mockSpeakers(page, {})
  await openSpeakers(page)
  await expect(page.getByTestId('manual-speakers')).toHaveCount(0)
  await expect(page.getByTestId('diarize-estimate')).toHaveText('Takes approx. 10 min to 20 min.')
  await expect(page.getByTestId('transcribe-estimate')).toContainText('Rough estimate')
  await expect(page.getByTestId('transcribe-estimate')).toContainText(' on ')
})
