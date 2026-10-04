import { expect, test, type Page } from '@playwright/test'
import { hitHeight, installHitArea } from './hitArea'

// .btn-sm keeps a 44px hit area but is 32px tall: measure the hit area, not the box.
test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

// U01: the Dub stage of a novel-narration drama warns about lines with no
// English yet, worded for the chosen narration language. Config, narration
// config and workflow progress are mocked; nothing is ever run.

const dubConfig = (over: object = {}) => ({
  drama_id: 1, content_mode: 'novel', is_narration: true, narration_language: 'translation',
  narration_language_options: ['translation', 'original'], source_language: 'zh',
  tts_engines: [{ key: 'edge_tts', label: 'Edge TTS', requires_internet: true }],
  defaults: null, speakers: [], gpu_required: false, speakable_line_count: 5, track_available: false,
  gpt_sovits_configured: false, can_keep_background: false, ...over,
})

const progress = (untranslated: number) => ({
  drama_id: 1, stage_index: 3, stage: 'dub', line_count: 5, untranslated_count: untranslated,
  flagged_count: 0, has_audio: false, has_dub_track: false, exported: false,
  stages: ['source', 'translate', 'review', 'dub', 'export'].map((key) => ({ key, state: key === 'dub' ? 'current' : 'done' })),
})

async function mockAll(page: Page, opts: { untranslated?: number; progressStatus?: number; config?: object } = {}) {
  await page.route('**/api/dub/dramas/1/config', (route) => route.fulfill({ json: dubConfig(opts.config) }))
  await page.route('**/api/dub/dramas/1/pacing', (route) =>
    route.fulfill({ json: { available: false, counts: {}, lines: [] } }))
  await page.route('**/api/narration/dramas/1/config', (route) =>
    route.fulfill({
      json: {
        drama_id: 1, is_narration: true, has_novel_source: true,
        engines: [{ key: 'fake', key_configured: true }], default_engine: 'fake',
        max_chunk_chars: 500, existing_line_count: 5, replaces_existing_lines: true, job_running: false,
      },
    }))
  await page.route('**/api/workflow/dramas/1/progress', (route) =>
    opts.progressStatus
      ? route.fulfill({ status: opts.progressStatus, json: { error: { code: 'internal', message: 'Something went wrong.' } } })
      : route.fulfill({ json: progress(opts.untranslated ?? 0) }))
}

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

test('English narration: warns the lines will be silent and links to Translate', async ({ page }) => {
  await mockAll(page, { untranslated: 3 })
  await page.goto('/#/drama/1/dub')
  const note = page.getByTestId('dub-untranslated')
  await expect(note).toHaveText(
    /^3 lines have no English yet and will be silent in the narration\. Translate them first\./,
  )
  await expect(note).toHaveAttribute('role', 'note')
  // Advisory only: Generate stays available.
  await expect(page.getByRole('button', { name: 'Generate dub' })).toBeEnabled()
  await note.getByRole('link', { name: 'Go to Translate' }).click()
  await expect(page).toHaveURL(/#\/drama\/1\/translate$/)
})

test('source-language narration: says subtitles will miss the English half, no link', async ({ page }) => {
  await mockAll(page, { untranslated: 1 })
  await page.goto('/#/drama/1/dub')
  const note = page.getByTestId('dub-untranslated')
  await expect(note).toContainText('1 line has no English yet')
  await page.getByRole('combobox', { name: 'Narration language' }).selectOption('original')
  await expect(note).toHaveText(
    '1 line has no translation yet. Narration will still generate for it (it speaks the source text), ' +
      'but its exported subtitles will be missing the English half of the bilingual pair. ' +
      'Translate first if you want complete subtitles.',
  )
  await expect(note.getByRole('link')).toHaveCount(0)
})

test('no warning when every line is translated, or when progress cannot be loaded', async ({ page }) => {
  await mockAll(page, { untranslated: 0 })
  await page.goto('/#/drama/1/dub')
  await expect(page.getByTestId('dub-summary')).toContainText('5 speakable lines')
  await expect(page.getByTestId('dub-untranslated')).toHaveCount(0)
  await page.getByRole('combobox', { name: 'Narration language' }).selectOption('original')
  await expect(page.getByTestId('dub-untranslated')).toHaveCount(0)

  await page.unrouteAll({ behavior: 'ignoreErrors' })
  await mockAll(page, { progressStatus: 500 })
  await page.goto('/#/drama/1/dub')
  await expect(page.getByTestId('dub-summary')).toContainText('5 speakable lines')
  await expect(page.getByRole('button', { name: 'Generate dub' })).toBeEnabled()
  await expect(page.getByTestId('dub-untranslated')).toHaveCount(0)
})

test('no warning for a timed (non-narration) dub', async ({ page }) => {
  await mockAll(page, { untranslated: 4, config: { is_narration: false, content_mode: null } })
  await page.goto('/#/drama/1/dub')
  await expect(page.getByTestId('dub-summary')).toContainText('5 speakable lines')
  await expect(page.getByTestId('dub-untranslated')).toHaveCount(0)
})

test.describe('phone', () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true })

  test('the warning fits a 390px width with a 44px Go to Translate target', async ({ page }) => {
    await mockAll(page, { untranslated: 12 })
    await page.goto('/#/drama/1/dub')
    const note = page.getByTestId('dub-untranslated')
    await expect(note).toContainText('12 lines have no English yet')
    const { scroll, client } = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      client: document.documentElement.clientWidth,
    }))
    expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
    const box = await note.getByRole('link', { name: 'Go to Translate' }).boundingBox()
    expect(box).not.toBeNull()
    expect(await hitHeight(note.getByRole('link', { name: 'Go to Translate' }))).toBeGreaterThanOrEqual(44)
    expect(box!.x + box!.width).toBeLessThanOrEqual(390)
    if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/dub-untranslated-phone.png`, fullPage: true })
  })
})
