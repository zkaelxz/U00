import { expect, test, type Page } from '@playwright/test'
import { mediaPanel, mediaTab } from './mediaTabs'
import { mockTranscription } from './transcriptionMissingMocks'
import { openTranscribeOptions, enableDeveloperMode, transcribeCard, openAdvanced } from './sourceHelpers'

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
  "Qwen3 forced alignment needs a transcript to align, but this title is in Whisper-text-only mode. Supply a transcript, or set alignment_method back to 'whisper_diff'."

test('a refused option is highlighted on its field, with the reason beside it', async ({ page }) => {
  await page.route('**/api/transcribe/dramas/1/run', (route) =>
    route.fulfill({
      status: 422,
      json: { error: { code: 'validation_error', message: QWEN_SENTENCE } },
    }),
  )
  await page.goto('/#/drama/1/source?tab=transcribe')
  await page.getByLabel('Transcript text').fill('line one')
  await page.getByRole('button', { name: 'Transcribe', exact: true }).click()

  const field = page.getByLabel('Alignment method', { exact: true })
  await expect(field).toBeVisible() // The field is on Tools, in a fold: the tab and fold are opened for you
  await expect(field).toHaveAttribute('aria-invalid', 'true')
  await expect(page.locator('.field-error', { hasText: 'Qwen3 forced alignment needs a transcript' })).toBeVisible()
  await expect(page.getByText('Fix the highlighted option, then transcribe again.')).toBeVisible()
  await expect(page.locator('.error-banner')).toHaveCount(0)
  await shot(page, 'transcribe-field-error-desktop')

  // Touching the field clears the flag.
  await field.selectOption('whisper_diff')
  await expect(field).not.toHaveAttribute('aria-invalid', 'true')
})

test('the form survives a reload, and Reset to defaults clears it', async ({ page }) => {
  await enableDeveloperMode(page)
  // A fresh context: no draft and every fold closed.
  await page.goto('/#/drama/1/source')
  await openTranscribeOptions(page)
  const run = transcribeCard(page)
  const tools = await openAdvanced(page)
  const beam = tools.getByLabel('Beam size', { exact: true })
  await expect(beam).toBeVisible()
  const savedBeam = await beam.inputValue()
  await beam.fill('7')
  await tools.getByLabel('Extra names to expect', { exact: true }).fill('沈清疑')
  // The prompt fold holds Extra names and the replacement prompt; openAdvanced opens it.
  await tools.getByLabel('Replacement prompt', { exact: true }).fill('full prompt')
  await openTranscribeOptions(page)
  await run.getByLabel('Source language').selectOption('ja')
  await run.getByLabel('Transcript text').fill('kept across a reload')
  await expect
    .poll(() => page.evaluate(() => localStorage.getItem('baihe.draft.1.transcribe')))
    .toContain('"beam_size":"7"')

  await page.reload()
  await openTranscribeOptions(page)
  await expect(run.getByLabel('Source language')).toHaveValue('ja')
  await expect(run.getByLabel('Transcript text')).toHaveValue('kept across a reload')
  const again = await openAdvanced(page)
  await expect(again.getByLabel('Beam size', { exact: true })).toHaveValue('7')
  await expect(again.getByLabel('Extra names to expect', { exact: true })).toHaveValue('沈清疑')
  await expect(again.getByLabel('Replacement prompt', { exact: true })).toHaveValue('full prompt')

  await again.getByRole('button', { name: 'Reset to defaults' }).click()
  await expect(again.getByLabel('Beam size', { exact: true })).toHaveValue(savedBeam)
  await expect(again.getByLabel('Extra names to expect', { exact: true })).toHaveValue('')
  await expect(again.getByLabel('Replacement prompt', { exact: true })).toHaveValue('')
  await openTranscribeOptions(page)
  await expect(run.getByLabel('Source language')).toHaveValue('zh')
  await expect(run.getByLabel('Transcript text')).toHaveValue('')
})

test('forced alignment is disabled with a reason when there is no supplied transcript', async ({ page }) => {
  await page.route('**/api/transcribe/dramas/1/config', async (route) => {
    const resp = await route.fetch()
    await route.fulfill({ response: resp, json: { ...(await resp.json()), transcript_mode: 'whisper', alignment_method: 'whisper_diff' } })
  })
  await page.goto('/#/drama/1/source')
  await openAdvanced(page)
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
  await page.goto('/#/drama/1/source?tab=transcribe')
  await page.getByLabel('Transcript text').fill('line one')
  await page.getByRole('button', { name: 'Transcribe', exact: true }).click()
  await expect(page.locator('.error-banner')).toContainText('Unknown source_language.')
})

test('the language and Whisper model are on Transcribe; the options are on Tools in folds', async ({ page }) => {
  await page.goto('/#/drama/1/source?tab=transcribe')
  const card = transcribeCard(page)
  await expect(page.getByTestId('settings-summary')).toBeVisible()
  await expect(card.getByLabel('Source language', { exact: true })).toBeVisible()
  await expect(card.getByLabel('Whisper model', { exact: true })).toBeVisible()
  await expect(card.getByLabel('Expected speakers', { exact: true })).toHaveCount(0)
  await expect(card.getByLabel('Beam size', { exact: true })).toHaveCount(0)
  await expect(card.getByRole('heading', { name: 'Transcribe' })).toHaveCount(0)
  await shot(page, 'transcribe-folded-desktop')
  await mediaTab(page, 'tools').click()
  const tools = mediaPanel(page, 'tools')
  // Options never saved: the Switches fold opens by itself and the others stay closed until opened.
  await expect(tools.getByRole('switch', { name: 'Use Groq' })).toBeVisible()
  await expect(tools.getByLabel('Expected speakers', { exact: true })).toBeHidden()
  await tools.locator('.section-title', { hasText: /^Speakers$/ }).click()
  await expect(tools.getByLabel('Expected speakers', { exact: true })).toBeVisible()
  // Remembered: still open after a reload. Section saves the state in its toggle
  // handler, which runs after the open attribute changes, so wait for the save.
  await expect
    .poll(() => page.evaluate(() => window.localStorage.getItem('baihe.section.source.advanced.speakers')))
    .toBe('1')
  await page.reload()
  await expect(tools.getByLabel('Expected speakers', { exact: true })).toBeVisible()
  // Developer Mode is off here, so the gated knobs are not in the DOM even with the fold open.
  await expect(tools.getByLabel('Beam size', { exact: true })).toHaveCount(0)
})

test('"Still needed" is a callout with its fix button', async ({ page }) => {
  await page.goto('/#/drama/1/source?tab=transcribe')
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
  await openAdvanced(page)
  await page.locator('.section-title', { hasText: /^Auto-tune min silence$/ }).click()
  await expect(page.getByTestId('autotune-elapsed')).toContainText(/0:0\d elapsed/)
  await expect(page.getByTestId('autotune-elapsed')).toContainText(/0:0[2-9] elapsed/)
})

test('the setup checklist sits above Transcribe so its reason is read first', async ({ page }) => {
  // Config, presets and diagnostics all say Whisper is missing, as on a fresh install.
  await mockTranscription(page, false)
  await page.goto('/#/drama/1/source?tab=transcribe')
  const button = page.getByRole('button', { name: 'Transcribe', exact: true })
  await expect(button).toBeDisabled()
  const checklist = page.locator('#transcribe-not-installed')
  await expect(checklist).toBeVisible()
  await expect(button).toHaveAttribute('aria-describedby', /transcribe-not-installed/)
  const [c, b] = [(await checklist.boundingBox())!, (await button.boundingBox())!]
  expect(c.y + c.height, 'checklist ends above the button').toBeLessThanOrEqual(b.y)
})
