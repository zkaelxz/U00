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
