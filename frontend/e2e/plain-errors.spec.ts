import { expect, test } from '@playwright/test'

import { mockDub, mockProgress } from './plainErrorsMocks'

// Plain reasons instead of generic or raw errors: Export and Dub buttons that
// cannot work yet are disabled with the reason; refusals read as sentences.

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

const openMedia = (page: import('@playwright/test').Page) => page.getByText('Video and audio', { exact: true }).click()

test('audiobook and dubbed video are disabled with the reason when there is no narration or dub', async ({ page }) => {
  await mockProgress(page)
  await page.goto('/#/drama/1/export')
  await openMedia(page)
  const audiobook = page.getByRole('group', { name: 'Audiobook' })
  await expect(audiobook.getByRole('button', { name: 'Start audiobook export' })).toBeDisabled()
  await expect(audiobook).toContainText('There is no narration yet. Create it in Dub first.')
  const dubbed = page.getByRole('group', { name: 'Video with the dub audio' })
  await expect(dubbed.getByRole('button', { name: 'Start dubbed video export' })).toBeDisabled()
  await expect(dubbed).toContainText('There is no dub yet. Create it in Dub first.')
})

test('with a narration and a dub the buttons are enabled', async ({ page }) => {
  await mockProgress(page, { has_dub_track: true, has_narration_track: true })
  await page.goto('/#/drama/1/export')
  await openMedia(page)
  await expect(page.getByRole('button', { name: 'Start audiobook export' })).toBeEnabled()
  await expect(page.getByRole('button', { name: 'Start dubbed video export' })).toBeEnabled()
})

test('a refused start shows the server sentence once, not the generic text', async ({ page }) => {
  await mockProgress(page, { has_narration_track: true })
  await page.route('**/api/export/dramas/1/audiobook', (route) =>
    route.fulfill({ status: 422, json: { error: { code: 'validation_error', message: 'There is no narration yet. Create it in Dub first.' } } }),
  )
  await page.goto('/#/drama/1/export')
  await openMedia(page)
  const start = page.getByRole('button', { name: 'Start audiobook export' })
  await start.click()
  await start.click()
  const alerts = page.getByRole('alert').filter({ hasText: 'There is no narration yet' })
  await expect(alerts).toHaveCount(1)
  await expect(page.getByText('Some of the values entered are not valid')).toHaveCount(0)
})

test('the soft-subtitle help says anything else becomes MKV', async ({ page }) => {
  await mockProgress(page)
  await page.goto('/#/drama/1/export')
  await openMedia(page)
  const group = page.getByRole('group', { name: 'Video with a subtitle track' })
  await expect(group).toContainText('MP4 and MKV keep their format, anything else becomes MKV.')
})

test('Generate dub is disabled with the reason when the voice package is missing', async ({ page }) => {
  await mockDub(page, 'The edge-tts package is not installed.')
  await page.goto('/#/drama/1/dub')
  await expect(page.getByRole('button', { name: 'Generate dub' })).toBeDisabled()
  await expect(page.getByTestId('dub-settings')).toContainText('The edge-tts package is not installed.')
})

test('a missing key has its own heading in Dub, not the missing-package one', async ({ page }) => {
  await mockDub(page, null)
  await page.route('**/api/dub/dramas/1/run', (route) =>
    route.fulfill({
      status: 503,
      json: { error: { code: 'dependency_unavailable', message: 'No claude key is configured. Set one in Settings first.', details: { reason: 'no_key', engine: 'claude' } } },
    }),
  )
  await page.goto('/#/drama/1/dub')
  await page.getByRole('button', { name: 'Generate dub' }).click()
  const alert = page.getByRole('alert').filter({ hasText: 'No key is set for Claude. Add it in Settings.' })
  await expect(alert).toBeVisible()
  await expect(page.getByText('not installed or not reachable')).toHaveCount(0)
})

test('Transcribe uses the stored media when a staged file is not set to replace it', async ({ page }) => {
  const runs: unknown[] = []
  await page.route('**/api/transcribe/dramas/1/config', async (route) => {
    const resp = await route.fetch()
    await route.fulfill({
      response: resp,
      json: { ...(await resp.json()), transcript_mode: 'whisper', asr_backend_choice: 'whisper', whisper_installed: true, audio_available: true },
    })
  })
  await page.route('**/api/media/dramas/1/status', (r) =>
    r.fulfill({ json: { drama_id: 1, has_audio: true, has_source_video: false, upload_max_mb: 500 } }))
  await page.route('**/api/transcribe/dramas/1/run', (r) => {
    runs.push(r.request().postDataJSON())
    return r.fulfill({ status: 503, json: { error: { code: 'dependency_unavailable', message: 'x' } } })
  })
  await page.goto('/#/drama/1/source')
  await page.locator('#source-media-file-1').setInputFiles({ name: 'other.mp3', mimeType: 'audio/mpeg', buffer: Buffer.from('x') })
  await expect(page.getByTestId('transcribe-staged-unused')).toContainText('not used for this run')
  const transcribe = page.getByRole('button', { name: 'Transcribe', exact: true })
  await expect(transcribe).toBeEnabled()
  await transcribe.click()
  await expect.poll(() => runs.length).toBe(1)
})
