import { expect, test } from '@playwright/test'

// Auto-fill and Analyze media: the Slice 37 endpoints are mocked (no LLM,
// no ffprobe); the drama read and its refetch hit the real seeded API.

test('auto-fill suggests, never pre-ticks a field that would overwrite, applies only the ticked ones', async ({ page }) => {
  const applied: unknown[] = []
  await page.route('**/api/metadata/dramas/1/autofill', (route) =>
    route.fulfill({
      json: { drama_id: 1, found: true, suggestion: { title_en: 'A Different Title', studio: 'Mock Studio' } },
    }),
  )
  await page.route('**/api/metadata/dramas/1/autofill/apply', async (route) => {
    applied.push(route.request().postDataJSON())
    await route.continue()
  })
  await page.goto('/#/drama/1/source')
  await page.locator('.section-title', { hasText: 'Auto-fill metadata' }).click()
  await page.getByRole('textbox', { name: 'Listing URL' }).fill('https://example.com/page')
  await page.getByRole('button', { name: 'Auto-fill', exact: true }).click()

  const list = page.getByRole('list', { name: 'Suggested metadata' })
  await expect(list).toContainText('replaces:')
  await expect(list.getByRole('checkbox', { name: /English title/ })).not.toBeChecked()
  await expect(list.getByRole('checkbox', { name: /Studio/ })).toBeChecked()
  await page.getByRole('button', { name: 'Apply selected' }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Metadata updated.' })).toBeVisible()
  expect(applied).toEqual([{ studio: 'Mock Studio' }])
})

test('ignore drops the suggestions without writing', async ({ page }) => {
  let writes = 0
  await page.route('**/api/metadata/dramas/1/autofill', (route) =>
    route.fulfill({ json: { drama_id: 1, found: true, suggestion: { studio: 'X' } } }),
  )
  await page.route('**/autofill/apply', (route) => { writes += 1; return route.abort() })
  await page.goto('/#/drama/1/source')
  await page.locator('.section-title', { hasText: 'Auto-fill metadata' }).click()
  await page.getByRole('textbox', { name: 'Or paste page text' }).fill('some listing text')
  await page.getByRole('button', { name: 'Auto-fill', exact: true }).click()
  await page.getByRole('button', { name: 'Ignore' }).click()
  await expect(page.getByRole('list', { name: 'Suggested metadata' })).toHaveCount(0)
  expect(writes).toBe(0)
})

test('analyze media shows a summary line and details', async ({ page }) => {
  await page.route('**/api/media/dramas/1/status', (route) =>
    route.fulfill({ json: { drama_id: 1, has_audio: true, has_source_video: false, upload_max_mb: 500 } }),
  )
  await page.route('**/api/metadata/dramas/1/analyze-media', (route) =>
    route.fulfill({
      json: { drama_id: 1, duration_seconds: 3725, has_video: false, has_audio: true, audio_track_count: 1, sample_rate: 44100 },
    }),
  )
  await page.goto('/#/drama/1/source')
  await page.locator('.section-title', { hasText: 'Analyze media' }).click()
  await page.getByRole('button', { name: 'Analyze media' }).click()
  await expect(page.getByTestId('analysis')).toContainText('44100 Hz')
  await page.locator('.section-title', { hasText: 'Analyze media' }).click() // collapse
  await expect(page.getByText('1:02:05 · audio only')).toBeVisible()
})

test('analyze media shows resolution, subtitle tracks, the suggested steps, and applies the content type (P05)', async ({ page }) => {
  const drama = await (await page.request.get('/api/library/dramas/1')).json()
  let mediaType = 'audio_drama'
  const writes: unknown[] = []
  await page.route('**/api/library/dramas/1', (r) => r.fulfill({ json: { ...drama, media_type: mediaType } }))
  await page.route('**/api/dramas/1/metadata', (r) => {
    writes.push(r.request().postDataJSON())
    mediaType = 'video_drama'
    return r.fulfill({ json: { ...drama, media_type: mediaType } })
  })
  await page.route('**/api/media/dramas/1/status', (route) =>
    route.fulfill({ json: { drama_id: 1, has_audio: true, has_source_video: true, upload_max_mb: 500 } }),
  )
  await page.route('**/api/metadata/dramas/1/analyze-media', (route) =>
    route.fulfill({
      json: {
        drama_id: 1, duration_seconds: 60, has_video: true, has_audio: true, audio_track_count: 1, sample_rate: 48000,
        width: 1920, height: 1080, fps: 29.97,
        subtitle_tracks: [{ index: 2, codec: 'ass', language: 'chi' }],
        suggested_pipeline: ['Import existing subtitle track (chi) instead of transcribing', 'Translate'],
        content_type_guess: 'video_drama', content_type_reason: 'has a video track',
      },
    }),
  )
  await page.goto('/#/drama/1/source')
  await page.locator('.section-title', { hasText: 'Analyze media' }).click()
  await page.getByRole('button', { name: 'Analyze media' }).click()
  const details = page.getByTestId('analysis')
  await expect(details).toContainText('1920×1080')
  await expect(details).toContainText('29.97 fps')
  await expect(details).toContainText('1 (Chinese ASS)')
  await expect(page.getByTestId('analysis-pipeline').getByRole('listitem')).toHaveCount(2)
  await expect(page.getByTestId('analysis-suggestion')).toContainText('Likely content type: Video drama (has a video track).')
  await page.getByRole('button', { name: 'Use this content type' }).click()
  await expect.poll(() => writes).toEqual([{ media_type: 'video_drama' }])
  await expect(page.getByRole('status').filter({ hasText: 'Media type set to Video drama.' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Use this content type' })).toHaveCount(0)
  await expect(page.getByTestId('analysis-suggestion')).toContainText('This drama already uses it.')
})
