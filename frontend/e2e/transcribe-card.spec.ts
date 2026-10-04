import { expect, test, type Page } from '@playwright/test'
import { openTranscribeOptions } from './sourceHelpers'

// The Transcribe card: a refused option is flagged on its own field, the
// settings fold once the drama has lines, "Still needed" is a callout, and
// auto-tune shows elapsed time. Reads hit the seeded API (drama 1, in
// "I have a transcript" mode, no lines); the run, progress and auto-tune
// status are mocked.

const SHOTS = process.env.SHOT_DIR

test.use({ viewport: { width: 1440, height: 900 } })

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true })
}

const QWEN_SENTENCE =
  "Qwen3 forced alignment needs a transcript to align, but this drama is in Whisper-text-only mode. Supply a transcript, or set alignment_method back to 'whisper_diff'."

test('a refused option is highlighted on its field, with the reason beside it', async ({ page }) => {
  await page.route('**/api/transcribe/dramas/1/run', (route) =>
    route.fulfill({
      status: 422,
      json: { error: { code: 'validation_error', message: QWEN_SENTENCE } },
    }),
  )
  await page.goto('/#/drama/1/source')
  await page.getByLabel('Transcript text').fill('line one')
  await page.getByRole('button', { name: 'Transcribe', exact: true }).click()

  const field = page.getByLabel('Alignment method', { exact: true })
  await expect(field).toBeVisible() // Advanced was folded: it is opened for you
  await expect(field).toHaveAttribute('aria-invalid', 'true')
  await expect(page.locator('.field-error', { hasText: 'Qwen3 forced alignment needs a transcript' })).toBeVisible()
  await expect(page.getByText('Fix the highlighted option, then transcribe again.')).toBeVisible()
  await expect(page.locator('.error-banner')).toHaveCount(0)
  await shot(page, 'transcribe-field-error-desktop')

  // Touching the field clears the flag.
  await field.selectOption('whisper_diff')
  await expect(field).not.toHaveAttribute('aria-invalid', 'true')
})

test('forced alignment is disabled with a reason when there is no supplied transcript', async ({ page }) => {
  await page.route('**/api/transcribe/dramas/1/config', async (route) => {
    const resp = await route.fetch()
    await route.fulfill({ response: resp, json: { ...(await resp.json()), transcript_mode: 'whisper', alignment_method: 'whisper_diff' } })
  })
  await page.goto('/#/drama/1/source')
  await page.getByLabel('Alignment method', { exact: true }).waitFor({ state: 'attached' })
  await expect(page.getByLabel('Alignment method', { exact: true }).locator('option[value="qwen3_forced_align"]')).toBeDisabled()
  await expect(page.getByText('for raw audio, pick Whisper or Qwen3-ASR')).toBeAttached()
})

test('an unnamed 422 shows the server sentence in the banner', async ({ page }) => {
  await page.route('**/api/transcribe/dramas/1/run', (route) =>
    route.fulfill({
      status: 422,
      json: { error: { code: 'validation_error', message: 'Unknown source_language.' } },
    }),
  )
  await page.goto('/#/drama/1/source')
  await page.getByLabel('Transcript text').fill('line one')
  await page.getByRole('button', { name: 'Transcribe', exact: true }).click()
  await expect(page.locator('.error-banner')).toContainText('Unknown source_language.')
})

test('the language is always in view; model, speakers and advanced start folded under More options', async ({ page }) => {
  await page.goto('/#/drama/1/source')
  const card = page.getByRole('region', { name: 'Transcribe' })
  await expect(page.getByTestId('settings-summary')).toBeVisible()
  await expect(card.getByLabel('Source language', { exact: true })).toBeVisible()
  await expect(card.getByLabel('Whisper model', { exact: true })).toBeHidden()
  await shot(page, 'transcribe-folded-desktop')
  await page.locator('.section-title', { hasText: /^More options$/ }).click()
  await expect(card.getByLabel('Whisper model', { exact: true })).toBeVisible()
  // Remembered: still open after a reload. Section saves the state in its toggle
  // handler, which runs after the open attribute changes, so wait for the save.
  await expect
    .poll(() => page.evaluate(() => window.localStorage.getItem('baihe.section.source.transcribe')))
    .toBe('1')
  await page.reload()
  await expect(card.getByLabel('Whisper model', { exact: true })).toBeVisible()
})

test('"Still needed" is a callout with its fix button', async ({ page }) => {
  await page.goto('/#/drama/1/source')
  const callout = page.locator('#transcribe-needed')
  await expect(callout).toContainText('Still needed: the transcript text.')
  await expect(callout.getByRole('button', { name: 'Paste transcript' })).toBeVisible()
  await shot(page, 'transcribe-callout-desktop')
})

test('a running auto-tune shows elapsed time and an estimate', async ({ page }) => {
  await page.route('**/api/media/dramas/1/status', (route) =>
    route.fulfill({ json: { drama_id: 1, has_audio: true, has_source_video: false, upload_max_mb: 500 } }),
  )
  await page.route('**/api/transcribe/dramas/1/autotune', (route) =>
    route.fulfill({
      json: {
        job_id: 'autotune_1', status: 'running', progress: 0.33,
        message: 'Testing candidate 1 of 3 (300ms)...', results: null, best_candidate_ms: null,
      },
    }),
  )
  await page.goto('/#/drama/1/source')
  await openTranscribeOptions(page)
  await page.locator('.section-title', { hasText: /^Advanced$/ }).first().click()
  await page.locator('.section-title', { hasText: /^Auto-tune min silence$/ }).click()
  await expect(page.getByTestId('autotune-elapsed')).toContainText(/0:0\d elapsed/)
  await expect(page.getByTestId('autotune-elapsed')).toContainText(/0:0[2-9] elapsed/)
})
