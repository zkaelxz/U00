import { clearCheckLines, GLOSSARY_SENTENCE, joinSeries, leaveSeries, mockPeaks, seedCheckLines, seedLines } from './compareSelectedHelpers'
import { expectNoSidewaysScroll } from './mockHelpers'
import { clearLines } from './resegmentLlmMocks'
import { serveJobs } from './workspaceJobPillMocks'
import type { Page } from '@playwright/test'

import { expect, test } from './fixtures'

// Audio and video titles have a Simple view (#/drama/1, no stage) beside today's workspace; the choice is
// remembered in the browser. Title 1 is the seeded audio drama, 3 the seeded video, 2 the novel.

const progress = (id: number, lines: number, untranslated: number) => ({
  drama_id: id, stage_index: 1, stage: 'translate', line_count: lines, untranslated_count: untranslated,
  flagged_count: 0, has_audio: false, has_dub_track: false, exported: false, stages: [],
})
// Lines for a title whose progress a spec mocks, so the title page's line list agrees with it; the queue is empty.
async function mockLines(page: Page, id: number, total: number, untranslated: number) {
  const line = (n: number) => ({
    id: 1000 + n, idx: n - 1, start: n * 2, end: n * 2 + 1.5, zh: `第${n}句`, en: n > total - untranslated ? '' : `Line ${n}`, speaker: null,
    speaker_manual: false, sfx: false, flag: null, flag_note: null, dub_filename: null, lang: null, reviewed: false, confidence: null,
  })
  await page.route(`**/api/review/dramas/${id}/lines?*`, (route) => {
    const only = new URL(route.request().url()).searchParams.get('only')
    const all = Array.from({ length: total }, (_, i) => line(i + 1))
    const lines = only === 'risk' ? [] : only === 'untranslated' ? all.filter((l) => !l.en) : all
    return route.fulfill({ json: {
      lines, page: 1, page_size: 40, total: lines.length, flagged_count: 0, untranslated_count: untranslated, unreviewed_count: total, shaky_count: 0, risk_count: 0,
    } })
  })
}
// A step link reads "Done · Add", "Now · Translate, 4 of 10 left" or just "Export": this matches the step's name in any of them.
const step = (name: string) => new RegExp(`(^|· )${name}(,|$)`)

test('Simple shows the five actions and the lines, and Full brings the stage tabs back', async ({ page }) => {
  await page.route('**/api/workflow/dramas/1/progress', (route) => route.fulfill({ json: progress(1, 10, 10) }))
  await mockLines(page, 1, 10, 10)
  await page.goto('/#/drama/1')
  await expect(page.getByRole('navigation', { name: 'Stages' })).toBeVisible()
  await page.getByRole('radio', { name: 'Simple', exact: true }).check({ force: true })
  await expect(page).toHaveURL(/#\/drama\/1$/)
  await expect(page.getByRole('navigation', { name: 'Stages' })).toHaveCount(0)
  // The step links open each stage; the current step's action is the main button, and Add media, Redo and Glossary are in the header.
  const tabs = page.getByRole('list', { name: 'Steps' })
  await expect(tabs.getByRole('link', { name: step('Add') })).toHaveAttribute('href', '#/drama/1/source?tab=add')
  await expect(tabs.getByRole('link', { name: step('Transcribe') })).toHaveAttribute('href', '#/drama/1/source?tab=transcribe')
  await expect(tabs.getByRole('link', { name: step('Translate') })).toHaveAttribute('href', '#/drama/1/translate')
  await expect(tabs.getByRole('link', { name: step('Check') })).toHaveAttribute('href', '#/drama/1/review')
  await expect(tabs.getByRole('link', { name: step('Export') })).toHaveAttribute('href', '#/drama/1/export')
  await expect(page.getByRole('link', { name: 'Translate 10 lines', exact: true })).toHaveAttribute('href', '#/drama/1/translate')
  await expect(page.getByRole('link', { name: 'Redo transcription' })).toHaveAttribute('href', '#/drama/1/source?tab=transcribe')
  await expect(page.getByRole('link', { name: 'Add media', exact: true })).toHaveAttribute('href', '#/drama/1/source?tab=add')
  await expect(page.getByRole('link', { name: 'Glossary' })).toHaveAttribute('href', '#/drama/1/translate?tab=glossary')
  // A title with lines opens on them, with Review's tools; the title panel is only for a title with no lines yet, and
  // a title in no series has no Episodes tab.
  await expect(page.getByRole('region', { name: 'Review' })).toBeVisible()
  await expect(page.getByRole('tablist', { name: 'Review tools' })).toBeVisible()
  await expect(page.getByRole('complementary', { name: 'Title panel' })).toHaveCount(0)
  await expect(page.getByRole('tab', { name: 'Episodes' })).toHaveCount(0)
  await page.getByRole('radio', { name: 'Full', exact: true }).check({ force: true })
  await expect(page.getByRole('navigation', { name: 'Stages' })).toBeVisible()
})

test('the choice is remembered, and a link that names a stage still opens that stage', async ({ page }) => {
  await page.route('**/api/workflow/dramas/3/progress', (route) => route.fulfill({ json: progress(3, 0, 0) }))
  // Without audio the Transcribe step stays a link to its stage (Add media is the main button); the seeded video may or may not have audio, so pin it.
  await page.route('**/api/transcribe/dramas/3/config', async (route) => {
    const resp = await route.fetch()
    await route.fulfill({ response: resp, json: { ...(await resp.json()), audio_available: false } })
  })
  await page.goto('/#/drama/3')
  await page.getByRole('radio', { name: 'Simple', exact: true }).check({ force: true })
  await expect(page.getByRole('list', { name: 'Steps' }).getByRole('link', { name: step('Transcribe') })).toHaveAttribute('href', '#/drama/3/source?tab=transcribe')
  await page.getByRole('tab', { name: 'Episodes' }).click()
  await expect(page.getByRole('list', { name: 'Episodes' })).toContainText('Not transcribed')
  await page.reload()
  await expect(page.getByRole('list', { name: 'Steps' })).toBeVisible()
  await page.goto('/#/drama/3/review')
  // The stage opens on a slim page: no stage tabs, a way back (Review is the title page's twin, so to the Library), and Full brings the tabs back.
  await expect(page.getByRole('link', { name: 'Back to Library' })).toHaveAttribute('href', '#/library')
  await expect(page.getByRole('navigation', { name: 'Stages' })).toHaveCount(0)
  // The step tabs stay above the open stage, as on the title page.
  await expect(page.getByRole('list', { name: 'Steps' })).toBeVisible()
  await page.getByRole('radio', { name: 'Full', exact: true }).check({ force: true })
  await expect(page.getByRole('navigation', { name: 'Stages' })).toBeVisible()
})

test('Simple leaves the advanced controls out of the stages, and Full brings them back', async ({ page }) => {
  await page.route('**/api/workflow/dramas/1/progress', (route) => route.fulfill({ json: progress(1, 10, 10) }))
  await page.goto('/#/drama/1')
  await page.getByRole('radio', { name: 'Simple', exact: true }).check({ force: true })
  await page.getByRole('list', { name: 'Steps' }).getByRole('link', { name: step('Export') }).click()
  await expect(page.getByRole('radio', { name: 'Simple', exact: true })).toBeChecked()
  await expect(page.getByRole('group', { name: 'Subtitle file' })).toBeVisible()
  await expect(page.getByRole('group', { name: 'Burned-in video' })).toHaveCount(0)
  await expect(page.getByRole('group', { name: 'EPUB' })).toHaveCount(0)
  await page.goto('/#/drama/1/translate')
  await expect(page.getByRole('tab', { name: 'Glossary' })).toBeVisible()
  await expect(page.getByRole('tab', { name: 'Characters' })).toHaveCount(0)
  await expect(page.getByText('More options')).toHaveCount(0)
  await page.getByRole('radio', { name: 'Full', exact: true }).check({ force: true })
  await expect(page.getByRole('tab', { name: 'Characters' })).toBeVisible()
  await expect(page.getByText('More options')).toBeVisible()
  await page.goto('/#/drama/1/source?tab=add')
  await expect(page.getByText('Keep original files')).toBeVisible()
  await page.getByRole('radio', { name: 'Simple', exact: true }).check({ force: true })
  await expect(page.getByText('Keep original files')).toHaveCount(0)
})

test('a novel opens on its page, and Full is the workspace; a comic gets a Simple page of its own', async ({ page }) => {
  await page.goto('/#/drama/2')
  await expect(page.getByRole('radio', { name: 'Simple', exact: true })).toBeChecked()
  await expect(page.getByRole('link', { name: 'Add chapters' })).toBeVisible()
  await page.getByRole('radio', { name: 'Full', exact: true }).check({ force: true })
  await expect(page.getByRole('navigation', { name: 'Stages' })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('radio', { name: 'Full', exact: true })).toBeChecked()
  await page.getByRole('radio', { name: 'Simple', exact: true }).check({ force: true })
  await expect(page.getByRole('link', { name: 'Add chapters' })).toBeVisible()
})

test('with audio attached and no lines, the main button starts the flow instead of linking to a stage', async ({ page }) => {
  await page.route('**/api/workflow/dramas/1/progress', (route) => route.fulfill({ json: progress(1, 0, 0) }))
  await page.route('**/api/transcribe/dramas/1/config', async (route) => {
    const resp = await route.fetch()
    await route.fulfill({ response: resp, json: { ...(await resp.json()), audio_available: true } })
  })
  await page.goto('/#/drama/1')
  await page.getByRole('radio', { name: 'Simple', exact: true }).check({ force: true })
  await expect(page.getByRole('button', { name: 'Transcribe and translate' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Transcribe and translate' })).toHaveCount(0)
})

test('a comic has a Simple page with Open in Scanlate and Add pages, and Full is the workspace', async ({ page }) => {
  await page.route('**/api/library/dramas/1', async (route) => {
    const resp = await route.fetch()
    await route.fulfill({ response: resp, json: { ...(await resp.json()), media_type: 'manhua' } })
  })
  await page.route('**/api/scanlate/dramas/1/pages', (route) => {
    const pg = (ordinal: number, chapter_id: string, has_regions = false) => ({ id: ordinal, ordinal, width: 1, height: 1, has_rendered: false, has_regions, image_version: null, chapter_id })
    return route.fulfill({ json: {
      drama_id: 1, media_type: 'manhua', reading_mode_default: 'vertical', page_count: 3,
      pages: [pg(1, 'a', true), pg(2, 'a'), pg(3, 'b')],
      chapters: [
        { id: 'a', title: 'Prologue', known: true, first_page: 1, page_count: 2, hidden_count: 0 },
        { id: 'b', title: '', known: true, first_page: 3, page_count: 1, hidden_count: 0 },
      ],
    } })
  })
  await page.goto('/#/drama/1')
  await expect(page.getByRole('navigation', { name: 'Stages' })).toBeVisible()
  await page.getByRole('radio', { name: 'Simple', exact: true }).check({ force: true })
  await expect(page.getByRole('navigation', { name: 'Stages' })).toHaveCount(0)
  await expect(page.getByRole('link', { name: /Prologue/ })).toHaveAttribute('href', '#/comic/1?page=1')
  await expect(page.getByRole('link', { name: /Chapter 2/ })).toHaveAttribute('href', '#/comic/1?page=3')
  await expect(page.getByText('2 pages · 1 translated')).toBeVisible()
  await expect(page.getByRole('link', { name: 'Open in Scanlate' })).toHaveAttribute('href', '#/comic/1')
  await expect(page.getByRole('link', { name: 'Add pages' })).toHaveAttribute('href', '#/drama/1/source?tab=add')
  await expect(page.getByRole('link', { name: 'Review' })).toHaveCount(0)
  await page.getByRole('radio', { name: 'Full', exact: true }).check({ force: true })
  await expect(page.getByRole('navigation', { name: 'Stages' })).toBeVisible()
})

test('a title in a series lists its episodes in order with each state', async ({ page }) => {
  await page.route('**/api/workflow/dramas/1/progress', (route) => route.fulfill({ json: progress(1, 10, 0) }))
  await mockLines(page, 1, 10, 0)
  await page.route('**/api/library/dramas/1', async (route) => {
    const resp = await route.fetch()
    await route.fulfill({ response: resp, json: { ...(await resp.json()), series_id: 7 } })
  })
  const t = (id: number, name: string, episode_number: number | null, line_count: number, translated_count: number) => ({
    id, title_en: name, title_zh: null, episode_number, line_count, translated_count,
  })
  // Deliberately out of order: the list sorts by episode number, with an unnumbered title last.
  await page.route('**/api/series/7', (route) =>
    route.fulfill({
      json: {
        id: 7, name: 'Series Seven',
        titles: [t(1, 'Episode Two', 2, 10, 10), t(31, 'Episode Three', 3, 0, 0), t(30, 'Episode One', 1, 8, 0)],
      },
    }),
  )
  await page.goto('/#/drama/1')
  await page.getByRole('radio', { name: 'Simple', exact: true }).check({ force: true })
  await page.getByRole('tab', { name: 'Episodes' }).click()
  const items = page.getByRole('list', { name: 'Episodes' }).getByRole('listitem')
  await expect(items).toHaveCount(3)
  await expect(items.nth(0)).toContainText('Episode One')
  await expect(items.nth(0)).toContainText('Transcribed · Not translated')
  await expect(items.nth(1)).toContainText('Episode Two')
  await expect(items.nth(1)).toContainText('Transcribed · Translated')
  await expect(items.nth(2)).toContainText('Episode Three')
  await expect(items.nth(2)).toContainText('Not transcribed')
})

test.describe('a browser that never chose a title view', () => {
  test.use({ storageState: { cookies: [], origins: [] } })

  test('opens audio, video and comic titles on Simple', async ({ page }) => {
    await page.goto('/#/drama/1')
    await expect(page.getByRole('radio', { name: 'Simple', exact: true })).toBeChecked()
    await expect(page.getByRole('navigation', { name: 'Stages' })).toHaveCount(0)
    await page.goto('/#/drama/3')
    await expect(page.getByRole('radio', { name: 'Simple', exact: true })).toBeChecked()
  })
})

test('Simple Translate says engine, style and variant in one line and Change opens the dropdowns', async ({ page }) => {
  await page.goto('/#/drama/1/translate')
  await page.getByRole('radio', { name: 'Simple', exact: true }).check({ force: true })
  const line = page.getByTestId('translate-summary')
  await expect(line).toContainText('English (US)')
  await expect(page.getByLabel('AI engine', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Estimate cost' })).toHaveCount(0)
  const change = page.locator('.translate-summary').getByRole('button')
  await change.click()
  await expect(page.getByLabel('AI engine', { exact: true })).toBeVisible()
  await expect(page.getByLabel('English variant', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Estimate cost' })).toBeVisible()
  await expect(change).toHaveText('Done')
  await change.click()
  await expect(page.getByLabel('AI engine', { exact: true })).toHaveCount(0)
  await page.getByRole('radio', { name: 'Full', exact: true }).check({ force: true })
  await expect(page.getByLabel('AI engine', { exact: true })).toBeVisible()
  await expect(page.getByTestId('translate-summary')).toHaveCount(0)
})

test('Simple lists the steps with the current one marked and its notes', async ({ page }) => {
  await page.route('**/api/workflow/dramas/1/progress', (route) => route.fulfill({ json: progress(1, 10, 4) }))
  await page.goto('/#/drama/1')
  await page.getByRole('radio', { name: 'Simple', exact: true }).check({ force: true })
  const steps = page.getByRole('list', { name: 'Steps' })
  await expect(steps.getByRole('listitem')).toHaveCount(5)
  await expect(steps.locator('[aria-current="step"]')).toHaveText('Now · Translate, 4 of 10 left')
  await expect(steps.getByRole('link')).toHaveText(['Done · Add', 'Done · Transcribe, 10 lines', 'Now · Translate, 4 of 10 left', /^Next · Check(, .+)?$/, 'Export'])
  // The longer guidance stays as each link's tooltip.
  await expect(steps.getByRole('link', { name: step('Export') })).toHaveAttribute('title', 'SRT, VTT, ASS or video')
})

test('with lines the title opens on them, with the step tabs above them and no preview or progress bar', async ({ page }) => {
  await page.route('**/api/workflow/dramas/1/progress', (route) => route.fulfill({ json: progress(1, 10, 4) }))
  await mockLines(page, 1, 10, 4)
  await page.goto('/#/drama/1')
  await page.getByRole('radio', { name: 'Simple', exact: true }).check({ force: true })
  const lines = page.getByRole('region', { name: 'Review' })
  await expect(lines).toBeVisible()
  // The Translate step: the lines open on All, where the lines without English are, not on the check queue.
  await expect(page.getByRole('group', { name: 'Lines to show' }).getByRole('radio', { name: /^All/ })).toBeChecked()
  await expect(page.getByRole('progressbar', { name: 'Lines translated' })).toHaveCount(0)
  await expect(page.getByRole('region', { name: 'First lines' })).toHaveCount(0)

  // Wide window: the step tabs run across the top, above the lines.
  await page.setViewportSize({ width: 1280, height: 900 })
  const steps = (await page.getByRole('list', { name: 'Steps' }).boundingBox())!
  const first = (await lines.boundingBox())!
  expect(steps.y + steps.height).toBeLessThanOrEqual(first.y)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})

test('an audio or video Simple stage keeps the title page step tabs above it, so no switching back to Full is needed', async ({ page }) => {
  await page.route('**/api/workflow/dramas/1/progress', (route) => route.fulfill({ json: progress(1, 10, 4) }))
  await page.goto('/#/drama/1')
  await page.getByRole('radio', { name: 'Simple', exact: true }).check({ force: true })
  await page.goto('/#/drama/1/translate')
  const steps = page.getByRole('list', { name: 'Steps' })
  await expect(steps.getByRole('link', { name: step('Check') })).toHaveAttribute('href', '#/drama/1/review')
  await expect(page.getByRole('navigation', { name: 'Title steps' })).toHaveCount(0)
  await expect(page.getByRole('navigation', { name: 'Stages' })).toHaveCount(0)
  await page.getByRole('link', { name: 'Back to title page' }).click()
  await expect(page).toHaveURL(/#\/drama\/1$/)
  await page.getByRole('list', { name: 'Steps' }).getByRole('link', { name: step('Check') }).click()
  await expect(page).toHaveURL(/#\/drama\/1\/review$/)
  await expect(page.getByRole('list', { name: 'Steps' })).toBeVisible()
  // Review is where the title page's lines are too, so it leads back to the Library.
  await page.getByRole('link', { name: 'Back to Library' }).click()
  await expect(page).toHaveURL(/#\/library$/)
})

test('Simple Review keeps the checks, Fix flagged lines, flags and Mark episode reviewed and the Replace button; Full keeps every tool group and the row-density, Go to and Shortcuts controls', async ({ page }) => {
  seedLines(3)
  await page.goto('/#/drama/3')
  await page.getByRole('radio', { name: 'Simple', exact: true }).check({ force: true })
  await page.goto('/#/drama/3/review')
  const tabs = page.getByRole('tablist', { name: 'Review tools' }).getByRole('tab')
  await expect(tabs).toHaveText(['Look for problems', 'Change many lines', 'Finish'])
  // The same tools the Check and Fix tabs held, and none of the others.
  const titles = page.locator('.review-toolbox-panel:not([hidden]) details.section:not(details.section details.section) > summary .section-title')
  await expect(page.getByRole('list', { name: 'AI checks to run' })).toBeVisible()
  await expect(titles).toHaveText(['Check options', 'Flag lines for review'])
  await tabs.filter({ hasText: 'Change many lines' }).click()
  await expect(page.getByRole('group', { name: 'Fix flagged lines' })).toBeVisible()
  await expect(titles).toHaveText(['Options'])
  await tabs.filter({ hasText: 'Finish' }).click()
  await expect(titles).toHaveText(['Mark episode reviewed'])
  // Simple opens on the check queue, whose bar holds only its switch; Replace… is with the filters under All.
  await page.getByRole('group', { name: 'Lines to show' }).getByRole('radio', { name: /^All/ }).check({ force: true })
  await expect(page.getByRole('button', { name: 'Replace…' })).toBeVisible()
  for (const name of ['Compact rows', 'Shortcuts']) await expect(page.getByRole('button', { name, exact: true })).toHaveCount(0)
  await expect(page.getByLabel('Go to line number')).toHaveCount(0)
  // A link naming a group, or an old tab, that Simple leaves out opens Look for problems instead of an empty page.
  for (const tools of ['records', 'history']) {
    await page.goto('about:blank')
    await page.goto(`/#/drama/3/review?tools=${tools}`)
    await expect(page.getByRole('tab', { name: 'Look for problems' })).toHaveAttribute('aria-selected', 'true')
  }

  await page.goto('/#/drama/3')
  await page.getByRole('radio', { name: 'Full', exact: true }).check({ force: true })
  await page.goto('/#/drama/3/review')
  await expect(tabs).toHaveText(['Look for problems', 'Change many lines', 'Records', 'Finish'])
  // Full has no check queue: it opens on All lines with the whole toolbar.
  await expect(page.getByRole('radio', { name: /^All lines/ })).toBeChecked()
  await expect(page.getByRole('group', { name: 'Lines to show' })).toHaveCount(0)
  for (const name of ['Compact rows', 'Shortcuts']) await expect(page.getByRole('button', { name, exact: true })).toBeVisible()
  await expect(page.getByLabel('Go to line number')).toBeVisible()
})

test('Simple Transcribe leaves the Advanced options out and Full keeps them', async ({ page }) => {
  await page.goto('/#/drama/1')
  await page.getByRole('radio', { name: 'Simple', exact: true }).check({ force: true })
  await page.goto('/#/drama/1/source?tab=transcribe')
  await expect(page.getByRole('button', { name: 'Transcribe', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Advanced options' })).toHaveCount(0)

  await page.goto('/#/drama/1')
  await page.getByRole('radio', { name: 'Full', exact: true }).check({ force: true })
  await page.goto('/#/drama/1/source?tab=transcribe')
  await expect(page.getByRole('heading', { name: 'Advanced options' })).toBeVisible()
})

test('without lines the title panel has Translate, Glossary, Episodes and Export, and a Video tab only when there is a source video', async ({ page }) => {
  const status = (video: boolean) => ({ has_audio: video, has_source_video: video })
  await page.route('**/api/workflow/dramas/1/progress', (route) => route.fulfill({ json: progress(1, 0, 0) }))
  await page.route('**/api/media/dramas/1/status', async (route) => {
    const resp = await route.fetch()
    await route.fulfill({ response: resp, json: { ...(await resp.json()), ...status(false) } })
  })
  await page.goto('/#/drama/1')
  await page.getByRole('radio', { name: 'Simple', exact: true }).check({ force: true })
  const tabs = page.getByRole('tablist', { name: 'Title panel sections' }).getByRole('tab')
  await expect(tabs).toHaveText(['Translate', 'Glossary', 'Episodes', 'Export'])
  await expect(page.getByTestId('panel-translate-summary')).not.toBeEmpty()
  await page.getByRole('tab', { name: 'Glossary' }).click()
  await expect(page.getByRole('link', { name: 'Open glossary' })).toHaveAttribute('href', '#/drama/1/translate?tab=glossary')
  await page.getByRole('tab', { name: 'Export' }).click()
  await expect(page.getByRole('tabpanel', { name: 'Export' }).getByRole('link', { name: 'Export' })).toHaveAttribute('href', '#/drama/1/export')

  await page.unroute('**/api/media/dramas/1/status')
  await page.route('**/api/media/dramas/1/status', async (route) => {
    const resp = await route.fetch()
    await route.fulfill({ response: resp, json: { ...(await resp.json()), ...status(true) } })
  })
  await page.reload()
  await expect(tabs).toHaveText(['Video', 'Translate', 'Glossary', 'Episodes', 'Export'])
  await expect(page.locator('video.title-panel-video')).toHaveAttribute('src', /\/api\/media\/dramas\/1\/video$/)
})

test.describe('the check queue', () => {
  test.afterAll(() => clearLines())
  const card = (page: Page, n: number) => page.locator('.review-line').filter({ has: page.locator('.review-idx', { hasText: new RegExp(`^#${n}$`) }) })
  const queueRadio = (page: Page) => page.getByRole('group', { name: 'Lines to show' }).getByRole('radio', { name: /^Need a look/ })
  const allRadio = (page: Page) => page.getByRole('group', { name: 'Lines to show' }).getByRole('radio', { name: /^All/ })
  const simple = async (page: Page) => {
    await page.goto('/#/drama/3')
    await page.getByRole('radio', { name: 'Simple', exact: true }).check({ force: true })
  }

  test('opens on the lines that need a look, says why, and moves on as each one is checked', async ({ page }) => {
    seedCheckLines({ untranslated: true })
    await simple(page)
    await page.goto('/#/drama/3/review')
    await expect(queueRadio(page)).toBeChecked()
    await expect(page.getByText('Need a look 3', { exact: true })).toBeVisible()
    await expect(page.getByText('All 24', { exact: true })).toBeVisible()
    await expect(page.getByTestId('queue-fine')).toHaveText('20 of 24 look fine')
    // Riskiest first: the glossary name, then the line Whisper was unsure of, then the fast one.
    await expect(page.locator('.review-line .review-idx')).toHaveText(['#24', '#7', '#12'])
    // The open card says why in a plain sentence; the closed ones in a few words, as a chip in their meta line.
    await expect(card(page, 24)).toHaveAttribute('aria-current', 'true')
    await expect(card(page, 24).getByTestId('queue-reason')).toHaveText(GLOSSARY_SENTENCE)
    await expect(card(page, 7).getByTestId('queue-reason')).toHaveText('Whisper was unsure (50% sure)')
    await expect(card(page, 12).getByTestId('queue-reason')).toHaveText('Fast to read, 25 characters a second')
    await expect(page.locator('.review-line').getByTestId('line-flag')).toHaveCount(0)
    await expect(card(page, 24).getByRole('link', { name: 'Open glossary' })).toHaveAttribute('href', '#/drama/3/translate?tab=glossary')
    // The English under its label, boxed like a field; the fitting action first, with the green finishing step last.
    await expect(card(page, 24).locator('.review-queue-label')).toHaveText('English')
    const box = card(page, 24).getByTestId('line-en')
    expect(await box.evaluate((el) => getComputedStyle(el).borderTopStyle)).toBe('solid')
    // Edit details and Dismiss flag are in the card's More sheet (and on D and F), not its row.
    await expect(card(page, 24).getByRole('toolbar').getByRole('button').or(card(page, 24).getByRole('toolbar').getByRole('link')))
      .toHaveText(['Open glossary', 'Improve translation', 'See the lines around it', 'Looks right, next'])
    await card(page, 24).getByRole('button', { name: 'More actions for line 24' }).click()
    for (const name of ['Edit details', 'Dismiss flag']) await expect(page.getByRole('dialog').getByRole('button', { name, exact: true })).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog')).toHaveCount(0)
    const done = card(page, 24).getByRole('button', { name: 'Looks right, next' })
    const [fill, ok] = await done.evaluate((el) => {
      const probe = document.createElement('span')
      probe.style.color = 'var(--ok)'
      document.body.append(probe)
      const okColour = getComputedStyle(probe).color
      probe.remove()
      return [getComputedStyle(el).backgroundColor, okColour]
    })
    expect(fill, 'Looks right, next is the green button').toBe(ok)
    const [doneBox, cardBox] = [await done.boundingBox(), await card(page, 24).boundingBox()]
    expect((cardBox?.x ?? 0) + (cardBox?.width ?? 0) - ((doneBox?.x ?? 0) + (doneBox?.width ?? 0)), 'Looks right, next sits at the far right').toBeLessThan(40)
    // The queue's bar is only its switch: the filters and the step buttons are under All.
    await expect(page.getByRole('radiogroup', { name: 'Show' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Next flagged ›' })).toHaveCount(0)

    // Footer: the rest of the title, the untranslated line, and Mark the rest held back by the unflagged #7.
    const footer = page.getByTestId('queue-footer')
    await expect(footer).toContainText('The other 20 lines have no warnings.')
    await expect(footer).toContainText('1 line has no English yet.')
    await expect(footer.getByRole('button', { name: 'Mark the rest as checked', exact: true })).toBeDisabled()
    await expect(page.getByTestId('mark-rest-reason')).toHaveText('Look at the lines Whisper was unsure of first: this would mark them checked too.')

    // Looks right, next checks the line and opens the next card; Undo brings the line back into the queue, open.
    await card(page, 24).getByRole('button', { name: 'Looks right, next' }).click()
    await expect(page.locator('.review-line .review-idx')).toHaveText(['#7', '#12'])
    await expect(card(page, 7)).toHaveAttribute('aria-current', 'true')
    // Open, the unsure line says it all and offers Re-transcribe, held back with the line menu's reason on a title without audio.
    await expect(card(page, 7).getByTestId('queue-reason')).toHaveText('Whisper was unsure of this line (50% sure)')
    await expect(card(page, 7).getByRole('button', { name: 'Re-transcribe…' })).toBeDisabled()
    await expect(card(page, 7).getByRole('toolbar')).toContainText('Needs this title’s audio or video.')
    await expect(page.getByText('Need a look 2', { exact: true })).toBeVisible()
    await expect(page.getByTestId('checked-undo')).toContainText('Line #24 marked checked.')
    await page.getByTestId('checked-undo').getByRole('button', { name: 'Undo' }).click()
    await expect(page.locator('.review-line .review-idx')).toHaveText(['#24', '#7', '#12'])
    await expect(card(page, 24)).toHaveAttribute('aria-current', 'true')

    // The English is edited in place, and Looks right, next saves it before checking the line.
    await card(page, 24).getByTestId('line-en').click()
    await card(page, 24).getByRole('textbox', { name: 'Translation' }).fill('Let them see what the Yiling Patriarch really is.')
    await card(page, 24).getByRole('button', { name: 'Looks right, next' }).click()
    await expect(page.locator('.review-line .review-idx')).toHaveText(['#7', '#12'])
    await expect(card(page, 7)).toHaveAttribute('aria-current', 'true')
    const saved = await (await page.request.get('/api/review/dramas/3/lines?page=1&page_size=40&only=all')).json()
    expect(saved.lines[23]).toMatchObject({ en: 'Let them see what the Yiling Patriarch really is.', reviewed: true })

    // R on an unchecked card does the same as Looks right, next.
    await card(page, 7).locator('.review-line-meta').click()
    await card(page, 7).focus()
    await page.keyboard.press('r')
    await expect(page.locator('.review-line .review-idx')).toHaveText(['#12'])
    await expect(card(page, 12)).toHaveAttribute('aria-current', 'true')

    // Only flagged lines are left in the queue, so Mark the rest can run; it asks first.
    const mark = footer.getByRole('button', { name: 'Mark the rest as checked', exact: true })
    await expect(mark).toBeEnabled()
    await mark.click()
    await footer.getByRole('button', { name: 'Confirm: record untouched lines as approved' }).click()
    await expect(footer).toContainText('Marked 20 lines reviewed')
    await expect(page.getByText('Need a look 1', { exact: true })).toBeVisible()
    await expect(page.locator('.review-line .review-idx')).toHaveText(['#12'])

    // Read them all switches to every line, with the filters back.
    await footer.getByRole('button', { name: 'Read them all' }).click()
    await expect(allRadio(page)).toBeChecked()
    await expect(page.getByRole('radio', { name: /^All lines/ })).toBeChecked()
    await expect(page.locator('.review-line')).toHaveCount(24)
    await expect(page.getByRole('button', { name: 'Replace…' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Next flagged ›' })).toBeVisible()
  })

  test('the Check button names the lines, opens the queue on its first card, and becomes Export once nothing needs a look', async ({ page }) => {
    seedCheckLines()
    await simple(page)
    const steps = page.getByRole('list', { name: 'Steps' })
    await expect(steps.locator('[aria-current="step"]')).toContainText('3 lines need a look')
    // The title page shows the lines, so the button opens the queue there instead of linking to Review.
    await expect(queueRadio(page)).toBeChecked()
    await allRadio(page).check({ force: true })
    await expect(page.locator('.review-line')).toHaveCount(24)
    await page.getByRole('button', { name: 'Check the 3 lines', exact: true }).click()
    await expect(queueRadio(page)).toBeChecked()
    await expect(card(page, 24)).toBeFocused()
    await expect(page).toHaveURL(/#\/drama\/3$/)
    await expect(page.getByRole('link', { name: 'Check the 3 lines', exact: true })).toHaveCount(0)
    // On the Review page the same button opens the queue there.
    await page.goto('/#/drama/3/review')
    await expect(queueRadio(page)).toBeChecked()
    // The button arrives with the title's progress and moves the switch along the row, so a press made before it lands on the button.
    await expect(page.getByRole('button', { name: 'Check the 3 lines', exact: true })).toBeVisible()
    await allRadio(page).check({ force: true })
    await expect(page.locator('.review-line')).toHaveCount(24)
    await page.getByRole('button', { name: 'Check the 3 lines', exact: true }).click()
    await expect(queueRadio(page)).toBeChecked()
    await expect(card(page, 24)).toBeFocused()

    seedCheckLines({ warnings: false })
    await page.goto('about:blank')
    await page.goto('/#/drama/3/review')
    await expect(page.getByText('Need a look 0', { exact: true })).toBeVisible()
    await expect(page.getByText('No unchecked line has a flag or low confidence.')).toBeVisible()
    await expect(page.getByTestId('queue-fine')).toHaveText('24 of 24 look fine')
    // Nothing needs a look, so no line can be marked unseen: Mark the rest is ready.
    await expect(page.getByRole('button', { name: 'Mark the rest as checked', exact: true })).toBeEnabled()
    await expect(page.getByRole('list', { name: 'Steps' }).locator('[aria-current="step"]')).toHaveText('Now · Check, nothing needs a look')
    await expect(page.getByRole('link', { name: 'Export', exact: true }).first()).toHaveAttribute('href', '#/drama/3/export')
    // The Review tools still show beside an empty queue.
    await expect(page.getByRole('tablist', { name: 'Review tools' })).toBeVisible()
  })

  test('Mark the rest is not offered from another view\'s lines while the queue is still loading', async ({ page }) => {
    seedCheckLines()
    await simple(page)
    await page.goto('/#/drama/3/review?focus=flagged')
    // Both flagged lines fit one page: read as the queue, this page alone would let Mark the rest run over the unsure #7.
    await expect(page.locator('.review-line .review-idx')).toHaveText(['#12', '#24'])
    let arrive = () => {}
    const held = new Promise<void>((resolve) => { arrive = resolve })
    await page.route(/\/lines\?page=1&page_size=40&only=risk/, async (route) => {
      await held
      await route.continue()
    })
    await queueRadio(page).check({ force: true })
    await expect(queueRadio(page)).toBeChecked()
    await expect(page.locator('.review-line .review-idx')).toHaveText(['#12', '#24'])
    await expect(page.getByTestId('queue-footer')).toHaveCount(0)
    arrive()
    await expect(page.locator('.review-line .review-idx')).toHaveText(['#24', '#7', '#12'])
    await expect(page.getByTestId('queue-footer').getByRole('button', { name: 'Mark the rest as checked', exact: true })).toBeDisabled()
    await expect(page.getByTestId('mark-rest-reason')).toHaveText('Look at the lines Whisper was unsure of first: this would mark them checked too.')
  })

  test('a link to a line or a filter wins over the queue', async ({ page }) => {
    seedCheckLines()
    await simple(page)
    // A Ctrl+K line hit opens Review already searched, over every line: line 5 is not in the queue.
    await page.goto('/#/drama/3/review?find=第5句')
    await expect(allRadio(page)).toBeChecked()
    await expect(page.getByRole('searchbox', { name: 'Search lines' })).toHaveValue('第5句')
    await expect(card(page, 5)).toBeVisible()
    await page.goto('about:blank')
    await page.goto('/#/drama/3/review?focus=flagged')
    await expect(page.getByRole('radio', { name: /^Flagged/ })).toBeChecked()
    await page.goto('about:blank')
    await page.goto('/#/drama/3/review?focus=all')
    await expect(page.getByRole('radio', { name: /^All lines/ })).toBeChecked()
    await expect(page.locator('.review-line')).toHaveCount(24)
    // Show on its page from a search result opens the line among all lines, not in the queue.
    await page.goto('about:blank')
    await page.goto('/#/drama/3/review?find=第5句')
    await card(page, 5).getByRole('button', { name: 'Show on its page: line 5' }).click()
    await expect(page.getByRole('radio', { name: /^All lines/ })).toBeChecked()
    await expect(card(page, 5)).toHaveAttribute('aria-current', 'true')
  })

  test('the five step links share one line at 1440 wide, with the open step outlined', async ({ page }) => {
    seedCheckLines({ untranslated: true })
    await simple(page)
    await page.setViewportSize({ width: 1440, height: 900 })
    const links = page.getByRole('list', { name: 'Steps' }).getByRole('link')
    const texts = ['Done · Add', 'Done · Transcribe, 24 lines', 'Now · Translate, 1 of 24 left', 'Next · Check, 3 lines need a look', 'Export']
    await expect(links).toHaveText(texts)
    const tops = await links.evaluateAll((els) => els.map((el) => Math.round(el.getBoundingClientRect().top)))
    expect(new Set(tops).size, `the step links' tops ${tops}`).toBe(1)
    for (let n = 0; n < 5; n++) expect((await links.nth(n).boundingBox())!.height).toBeGreaterThanOrEqual(44)
    const outlined = page.locator('.title-step.is-marked')
    // On the title page no stage is open, so the current step is outlined; on a stage page, the stage's own step.
    await expect(outlined).toHaveCount(1)
    await expect(outlined).toHaveText('Now · Translate, 1 of 24 left')
    expect(await outlined.getByRole('link').evaluate((el) => getComputedStyle(el).borderTopWidth)).toBe('2px')
    await page.goto('/#/drama/3/review')
    await expect(links).toHaveText(texts)
    await expect(outlined).toHaveText('Next · Check, 3 lines need a look')
    await page.goto('/#/drama/3/export')
    await expect(outlined).toHaveText('Export')
    await expectNoSidewaysScroll(page)
  })

  test('the header holds Add media, Redo transcription, Glossary, the switch and More, with no second row of links', async ({ page }) => {
    seedCheckLines()
    await simple(page)
    const header = page.locator('.workspace-header')
    await expect(header.getByRole('link', { name: 'Add media', exact: true })).toHaveAttribute('href', '#/drama/3/source?tab=add')
    await expect(header.getByRole('link', { name: 'Redo transcription', exact: true })).toHaveAttribute('href', '#/drama/3/source?tab=transcribe')
    await expect(header.getByRole('link', { name: 'Glossary', exact: true })).toHaveAttribute('href', '#/drama/3/translate?tab=glossary')
    await expect(header.getByRole('radio', { name: 'Simple', exact: true })).toBeChecked()
    for (const name of ['Add media', 'Redo transcription', 'Glossary']) await expect(page.getByRole('link', { name, exact: true })).toHaveCount(1)
    // The main button is under the steps, first in the lines' Need a look row.
    const main = page.getByRole('button', { name: 'Check the 3 lines', exact: true })
    await expect(main).toHaveCount(1)
    expect((await main.boundingBox())!.y).toBeGreaterThan((await page.getByRole('list', { name: 'Steps' }).boundingBox())!.y)
    const more = header.getByRole('button', { name: 'More', exact: true })
    await expect(page.getByRole('button', { name: 'Open folder' })).toBeHidden()
    await more.click()
    await expect(more).toHaveAttribute('aria-expanded', 'true')
    await expect(page.getByRole('button', { name: 'Open folder' })).toBeVisible()
    // The source page link, or the way to add one when the title has none.
    await expect(page.locator('.title-top-more-row').locator('.ws-source, .ws-source-add')).toBeVisible()
    await more.click()
    await expect(page.getByRole('button', { name: 'Open folder' })).toBeHidden()
    // The stage pages share the header.
    await page.goto('/#/drama/3/translate')
    await expect(header.getByRole('link', { name: 'Glossary', exact: true })).toHaveAttribute('href', '#/drama/3/translate?tab=glossary')
    await expect(header.getByRole('button', { name: 'More', exact: true })).toBeVisible()
    await expectNoSidewaysScroll(page)
  })

  test('on Review the main button leads the Need a look row, and without lines it keeps its own row', async ({ page }) => {
    seedCheckLines()
    await simple(page)
    await page.goto('/#/drama/3/review')
    const main = page.getByRole('button', { name: 'Check the 3 lines', exact: true })
    const switchRow = page.getByRole('group', { name: 'Lines to show' })
    await expect(switchRow).toBeVisible()
    await expect(main).toHaveCount(1)
    const m = (await main.boundingBox())!
    const sw = (await switchRow.boundingBox())!
    expect(m.x + m.width, 'the button is left of the switch').toBeLessThanOrEqual(sw.x)
    expect(Math.abs((m.y + m.height / 2) - (sw.y + sw.height / 2)), 'the button and the switch share a row').toBeLessThan(m.height / 2)
    await expect(page.locator('.review-queue-row').getByRole('button', { name: 'Check the 3 lines', exact: true })).toHaveCount(1)

    clearLines()
    await page.route('**/api/transcribe/dramas/3/config', async (route) => {
      const resp = await route.fetch()
      await route.fulfill({ response: resp, json: { ...(await resp.json()), audio_available: false } })
    })
    await page.goto('about:blank')
    await page.goto('/#/drama/3/review')
    await expect(page.getByRole('list', { name: 'Steps' }).getByRole('link').first()).toHaveText('Now · Add')
    const add = page.getByRole('link', { name: 'Add media', exact: true })
    await expect(add).toHaveCount(1)
    await expect(add).toHaveClass(/btn-primary/)
  })

  test('on an audio title the player folds away and This line sits beside the cards', async ({ page }) => {
    seedCheckLines({ audio: true })
    await mockPeaks(page)
    await page.setViewportSize({ width: 1440, height: 900 })
    await simple(page)
    await page.goto('/#/drama/3/review')
    await expect(card(page, 24)).toHaveAttribute('aria-current', 'true')
    // No player between the switch row and the first card.
    const player = page.getByRole('group', { name: 'Player' })
    await expect(player).toBeHidden()
    await expect(page.getByRole('button', { name: /Show waveform|Hide waveform/ })).toBeHidden()
    const toolbar = (await page.locator('.review-toolbar').boundingBox())!
    const first = (await page.locator('.review-line').first().boundingBox())!
    expect(first.y - (toolbar.y + toolbar.height), 'the first card sits right under the switch row').toBeLessThanOrEqual(16)
    expect(first.y).toBeGreaterThanOrEqual(toolbar.y + toolbar.height)
    // The open card: no tick box, a short reason (the panel has the rest), and its four buttons.
    await expect(card(page, 24).locator('.review-select')).toBeHidden()
    await expect(card(page, 24).getByTestId('queue-reason')).toHaveText('Glossary name not used: 夷陵老祖')
    await expect(card(page, 24).getByRole('toolbar').getByRole('button').or(card(page, 24).getByRole('toolbar').getByRole('link')))
      .toHaveText(['▶ Play', 'Open glossary', 'Improve translation', 'Looks right, next'])

    // This line is the open tab, with the sound, the reason and the lines around it.
    const lineTab = page.getByRole('tab', { name: 'This line' })
    await expect(page.getByRole('tablist', { name: 'Review tools' }).getByRole('tab').first()).toHaveText('This line')
    await expect(lineTab).toHaveAttribute('aria-selected', 'true')
    const panel = page.getByTestId('this-line')
    await expect(panel.getByRole('heading')).toHaveText(['Line 24 sound', 'Why it is listed', 'Around it'])
    await expect(panel.getByTestId('this-line-wave')).toBeVisible()
    await expect(panel.getByTestId('this-line-why')).toHaveText(GLOSSARY_SENTENCE)
    await expect(panel.getByRole('link', { name: 'Open glossary' })).toHaveAttribute('href', '#/drama/3/translate?tab=glossary')
    // #24 is the last line: the line before it and itself.
    await expect(panel.getByTestId('this-line-around').locator('.this-line-near-idx')).toHaveText(['#23', '#24'])
    expect(await page.evaluate(() => localStorage.getItem('baihe.pref.review.tools.tab.3'))).toBeNull()
    await panel.getByRole('button', { name: '▶ Play' }).click()
    await expect.poll(() => page.locator('audio').evaluate((a: HTMLAudioElement) => !a.paused)).toBe(true)
    await expect(panel.locator('.this-line-playhead'), 'the playhead shows on the line\'s sound while it plays').toBeVisible()
    await page.keyboard.press('Alt+Space')

    // Another tab is remembered as before; All takes This line away (the player stays folded there too), and Need a
    // look brings it back open.
    await page.getByRole('tab', { name: 'Look for problems' }).click()
    await expect(lineTab).toHaveAttribute('aria-selected', 'false')
    expect(await page.evaluate(() => localStorage.getItem('baihe.pref.review.tools.tab.3'))).toBe('"problems"')
    await allRadio(page).check({ force: true })
    await expect(lineTab).toHaveCount(0)
    await expect(page.getByRole('tab', { name: 'Look for problems' })).toHaveAttribute('aria-selected', 'true')
    await expect(player).toBeHidden()
    await queueRadio(page).check({ force: true })
    await expect(lineTab).toHaveAttribute('aria-selected', 'true')
    await expect(player).toBeHidden()

    // The Player button brings the strip and the waveform back, and is remembered.
    const toggle = page.locator('.review-queue-row').getByRole('button', { name: 'Player', exact: true })
    await expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await expect(toggle).toHaveAttribute('title', 'Seek bar, speed, subtitles and the waveform')
    await toggle.click()
    await expect(player).toBeVisible()
    await expect(page.getByRole('button', { name: /Show waveform|Hide waveform/ })).toBeVisible()
    await page.reload()
    await expect(toggle).toHaveAttribute('aria-expanded', 'true')
    await expect(player).toBeVisible()
    await toggle.click()
    await expect(player).toBeHidden()

    // A neighbour opens all lines on that line.
    await card(page, 7).locator('.review-zh').click()
    await expect(card(page, 7)).toHaveAttribute('aria-current', 'true')
    await expect(panel.getByTestId('this-line-around').locator('.this-line-near-idx')).toHaveText(['#6', '#7', '#8'])
    await expect(panel.getByTestId('this-line-why')).toHaveText('Whisper was unsure of this line (50% sure)')
    await panel.getByRole('button', { name: /^#6 / }).click()
    await expect(allRadio(page)).toBeChecked()
    await expect(card(page, 6)).toHaveAttribute('aria-current', 'true')
    await expectNoSidewaysScroll(page)
  })

  test('without peaks the sound card keeps Play and the times, with no picture and no error', async ({ page }) => {
    seedCheckLines({ audio: true })
    await mockPeaks(page, true)
    await page.setViewportSize({ width: 1440, height: 900 })
    await simple(page)
    await page.goto('/#/drama/3/review')
    const panel = page.getByTestId('this-line')
    await expect(panel.getByRole('heading', { name: 'Line 24 sound' })).toBeVisible()
    await expect(panel.getByRole('button', { name: '▶ Play' })).toBeVisible()
    await expect(panel).toContainText('0:46.00')
    await expect(panel.getByTestId('this-line-wave')).toHaveCount(0)
    await expect(panel.getByRole('alert')).toHaveCount(0)
  })

  test('narrower than 1400px there is no This line: the open card says it all and offers the lines around it', async ({ page }) => {
    seedCheckLines({ audio: true })
    await mockPeaks(page)
    await page.setViewportSize({ width: 1100, height: 800 })
    await simple(page)
    await page.goto('/#/drama/3/review')
    await expect(card(page, 24)).toHaveAttribute('aria-current', 'true')
    await expect(page.getByRole('tab', { name: 'This line' })).toHaveCount(0)
    await expect(card(page, 24).getByTestId('queue-reason')).toHaveText(GLOSSARY_SENTENCE)
    await expect(card(page, 24).getByRole('button', { name: 'See the lines around it' })).toBeVisible()
    // A closed card is one row: its number, source, English and reason chip share a line.
    const closed = card(page, 12)
    const tops = await Promise.all(['.review-idx', '.review-zh', '[data-testid="line-en"]', '[data-testid="queue-reason"]'].map(async (sel) => (await closed.locator(sel).boundingBox())!.y))
    expect(Math.max(...tops) - Math.min(...tops), `the closed card's parts' tops ${tops}`).toBeLessThan(12)
    await expectNoSidewaysScroll(page)
  })

  // The title page and Review are one page once the title has lines; only the outlined step, the view the lines open on and
  // (on a phone) where the main button sits may differ.
  const address = (path: string) => new RegExp(`${path.replace(/\//g, '\\/')}$`)

  test('with lines that need a look, the title page is the check queue, and Review shows the same page', async ({ page }) => {
    seedCheckLines({ audio: true })
    await mockPeaks(page)
    await page.setViewportSize({ width: 1440, height: 900 })
    await simple(page)
    for (const path of ['#/drama/3', '#/drama/3/review']) {
      await page.goto('about:blank')
      await page.goto(`/${path}`)
      await expect(page.locator('.title-step.is-marked')).toHaveText('Now · Check, 3 lines need a look')
      const back = page.getByRole('link', { name: 'Back to Library' })
      await expect(back).toHaveAttribute('href', '#/library')
      await expect(back).toHaveText('‹Library')
      await expect(page.locator('.workspace-header').getByTestId('title-kind')).toHaveText('Video drama · Korean to English')
      // The main button is drawn once, as the first thing in the Need a look row.
      const main = page.getByRole('button', { name: 'Check the 3 lines', exact: true })
      await expect(main).toHaveCount(1)
      expect(await page.locator('.review-queue-row').evaluate((row) => row.querySelector('button, a')?.textContent)).toBe('Check the 3 lines')
      await expect(queueRadio(page)).toBeChecked()
      await expect(card(page, 24)).toHaveAttribute('aria-current', 'true')
      await expect(page.getByRole('tab', { name: 'This line' })).toHaveAttribute('aria-selected', 'true')
      const [open, line] = [(await card(page, 24).boundingBox())!, (await page.getByTestId('this-line').boundingBox())!]
      expect(line.x, 'This line sits beside the cards').toBeGreaterThanOrEqual(open.x + open.width)
      // Switching the view and pressing the button keep the address; the button opens the queue on its first card in place.
      await allRadio(page).check({ force: true })
      await expect(page.locator('.review-line')).toHaveCount(24)
      await expect(page).toHaveURL(address(path))
      await main.click()
      await expect(queueRadio(page)).toBeChecked()
      await expect(card(page, 24)).toBeFocused()
      await expect(page).toHaveURL(address(path))
      await expectNoSidewaysScroll(page)
    }
  })

  test('on the Translate step the title page opens on All, a link that names lines still wins, and Review opens on Need a look', async ({ page }) => {
    seedCheckLines({ untranslated: true })
    await simple(page)
    await expect(allRadio(page)).toBeChecked()
    await expect(page.locator('.review-line')).toHaveCount(24)
    await expect(page.locator('.title-step.is-marked')).toHaveText('Now · Translate, 1 of 24 left')
    const main = page.getByRole('link', { name: 'Translate 1 line', exact: true })
    await expect(main).toHaveCount(1)
    await expect(page.locator('.review-queue-row').getByRole('link', { name: 'Translate 1 line', exact: true })).toHaveAttribute('href', '#/drama/3/translate')
    await queueRadio(page).check({ force: true })
    await expect(page.locator('.review-line .review-idx')).toHaveText(['#24', '#7', '#12'])
    await expect(page).toHaveURL(address('#/drama/3'))

    await page.goto('about:blank')
    await page.goto('/#/drama/3?focus=flagged')
    await expect(page.getByRole('radio', { name: /^Flagged/ })).toBeChecked()
    await page.goto('about:blank')
    await page.goto('/#/drama/3?find=第5句')
    await expect(page.getByRole('searchbox', { name: 'Search lines' })).toHaveValue('第5句')
    await expect(card(page, 5)).toBeVisible()

    await page.goto('about:blank')
    await page.goto('/#/drama/3/review')
    await expect(queueRadio(page)).toBeChecked()
    await expect(page.locator('.title-step.is-marked')).toHaveText('Next · Check, 3 lines need a look')
    await allRadio(page).check({ force: true })
    await expect(page.locator('.review-line')).toHaveCount(24)
    await expect(page).toHaveURL(address('#/drama/3/review'))
  })

  test('a job that finishes while the title page shows its panel brings the lines in, with no reload', async ({ page }) => {
    seedCheckLines()
    let lines = 0
    await page.route('**/api/workflow/dramas/3/progress', (route) => route.fulfill({ json: progress(3, lines, 0) }))
    await page.route('**/api/transcribe/dramas/3/config', async (route) => {
      const resp = await route.fetch()
      await route.fulfill({ response: resp, json: { ...(await resp.json()), audio_available: true } })
    })
    const now = () => Math.floor(Date.now() / 1000)
    const job = (over: Record<string, unknown> = {}) => ({
      job_id: 'transcribe_3', status: 'running', progress: 0.5, message: 'Transcribing', error: null, description: 'Transcribe Signal',
      gpu_touching: true, started_at: now() - 30, finished_at: null, updated_at: now(), owned_by_me: true, drama_id: 3, kind: 'transcribe', ...over,
    })
    // Started on the Transcribe step's page; the person came back to the title page while it runs.
    const jobs = { current: [job()] }
    await serveJobs(page, jobs)
    await simple(page)
    const steps = page.getByRole('list', { name: 'Steps' })
    await expect(page.getByRole('complementary', { name: 'Title panel' })).toBeVisible()
    await expect(steps.locator('[aria-current="step"]')).toHaveText('Now · Transcribe')
    await expect(page.getByTestId('job-pill')).toHaveText(/^● Transcribing/)
    await page.evaluate(() => { (window as { stayed?: boolean }).stayed = true })

    lines = 24
    jobs.current = [job({ status: 'done', progress: 1, outcome: 'ok', finished_at: now() })]
    // The jobs list is read again when the window regains focus, so the finish is seen without waiting for a poll.
    await page.evaluate(() => window.dispatchEvent(new Event('focus')))
    await expect(page.getByTestId('job-pill')).toHaveText('✓ Done')
    await expect(page.getByRole('region', { name: 'Review' })).toBeVisible()
    await expect(page.getByRole('complementary', { name: 'Title panel' })).toHaveCount(0)
    await expect(steps.locator('[aria-current="step"]')).toHaveText(/^Now · Check/)
    expect(await page.evaluate(() => (window as { stayed?: boolean }).stayed), 'the page was not reloaded').toBe(true)
  })

  test('a count refresh that fails keeps the step row\'s words and the main button the page had', async ({ page }) => {
    seedCheckLines()
    await simple(page)
    const steps = page.getByRole('list', { name: 'Steps' })
    await expect(steps.locator('[aria-current="step"]')).toHaveText('Now · Check, 3 lines need a look')
    await expect(page.getByRole('button', { name: 'Check the 3 lines', exact: true })).toBeVisible()

    await page.route('**/api/workflow/dramas/3/progress', (route) => route.fulfill({ status: 500, json: { detail: 'Database is locked' } }))
    const failed = page.waitForResponse((r) => new URL(r.url()).pathname === '/api/workflow/dramas/3/progress' && r.status() === 500)
    const risk = page.waitForResponse((r) => new URL(r.url()).pathname === '/api/review/dramas/3/lines' && new URL(r.url()).searchParams.get('page_size') === '1')
    await card(page, 24).getByRole('button', { name: 'Looks right, next' }).click()
    await Promise.all([failed, risk])
    // Both answers have been handled by the next frame.
    await page.evaluate(() => new Promise((done) => requestAnimationFrame(() => setTimeout(done, 0))))
    await expect(card(page, 7)).toHaveAttribute('aria-current', 'true')
    await expect(steps.getByRole('link')).toHaveText(['Done · Add', 'Done · Transcribe, 24 lines', 'Done · Translate', 'Now · Check, 2 lines need a look', 'Next · Export'])
    await expect(page.getByRole('button', { name: 'Check the 2 lines', exact: true })).toBeVisible()
    await expect(page.getByRole('region', { name: 'Review' })).toBeVisible()
  })

  test('with no lines the title page keeps the title panel and has no lines area', async ({ page }) => {
    clearLines()
    await simple(page)
    await expect(page.getByRole('complementary', { name: 'Title panel' })).toBeVisible()
    await expect(page.getByRole('tablist', { name: 'Title panel sections' }).getByRole('tab')).toContainText(['Translate', 'Glossary', 'Episodes', 'Export'])
    await expect(page.getByRole('region', { name: 'Review' })).toHaveCount(0)
    await expect(page.getByRole('tablist', { name: 'Review tools' })).toHaveCount(0)
    await expect(page.getByRole('link', { name: 'Back to Library' })).toHaveAttribute('href', '#/library')
  })

  test('the one-button flow keeps its button, not a new one, when the first lines arrive', async ({ page }) => {
    seedCheckLines()
    let lines = 0
    await page.route('**/api/workflow/dramas/3/progress', (route) => route.fulfill({ json: progress(3, lines, lines) }))
    await page.route('**/api/transcribe/dramas/3/config', async (route) => {
      const resp = await route.fetch()
      await route.fulfill({ response: resp, json: { ...(await resp.json()), audio_available: true } })
    })
    // Transcribing writes the lines; the glossary run then stays running, so the flow waits on it with its progress text.
    await page.route('**/api/transcribe/dramas/3/run', (route) => {
      lines = 24
      return route.fulfill({ json: { job_id: 'e2e-flow' } })
    })
    await page.route('**/api/jobs/e2e-flow', (route) => route.fulfill({ json: {
      job_id: 'e2e-flow', status: 'done', progress: null, message: '', error: null, description: null,
      gpu_touching: false, started_at: 1, finished_at: 2, updated_at: 2,
    } }))
    await page.route('**/api/glossary/dramas/3/from-lines', (route) => route.fulfill({ json: { status: 'running', message: '', run_id: 'e2e' } }))
    await simple(page)
    await expect(page.getByRole('complementary', { name: 'Title panel' })).toBeVisible()
    const flow = page.getByRole('button', { name: 'Transcribe and translate', exact: true })
    const before = await flow.elementHandle()
    await flow.click()
    await expect(page.getByRole('region', { name: 'Review' })).toBeVisible()
    await expect(page.getByRole('complementary', { name: 'Title panel' })).toHaveCount(0)
    await expect(page.locator('.title-main-row [role="status"]')).toHaveText('Finding names for the glossary')
    await expect(flow).toBeDisabled()
    expect(await before!.evaluate((el) => el.isConnected && el.textContent), 'the same button, still on the page').toBe('Transcribe and translate')
  })

  // The closed cards of the queue on a wide screen: what tells the lines apart, with the room going to the texts.
  test('a closed card gives its source and English the room: no speaker or badges until it opens, the chip narrow, nothing cut', async ({ page }) => {
    seedCheckLines({ audio: true, real: true })
    await mockPeaks(page)
    await simple(page)
    await page.goto('/#/drama/3/review')
    await expect(card(page, 24)).toHaveAttribute('aria-current', 'true')
    const closed = card(page, 12)
    const [zh, en, chip] = [closed.locator('.review-zh'), closed.getByTestId('line-en'), closed.getByTestId('queue-reason')]
    for (const width of [1100, 1280, 1440, 1920]) {
      await page.setViewportSize({ width, height: 900 })
      for (const [what, loc] of [['source', zh], ['English', en]] as const) {
        expect((await loc.boundingBox())!.width, `the ${what} column at ${width}px`).toBeGreaterThanOrEqual(150)
      }
      const enLines = await en.evaluate((el) => {
        const r = document.createRange()
        r.selectNodeContents(el)
        return new Set([...r.getClientRects()].map((x) => Math.round(x.top))).size
      })
      expect(enLines, `the English's lines at ${width}px`).toBeLessThanOrEqual(4)
      const tops = await Promise.all([closed.locator('.review-idx'), zh, en, chip].map(async (loc) => (await loc.boundingBox())!.y))
      expect(Math.max(...tops) - Math.min(...tops), `the closed card's parts' tops at ${width}px: ${tops}`).toBeLessThan(12)
      for (const [what, loc] of [['source', zh], ['English', en], ['chip', chip]] as const) {
        expect(await loc.evaluate((el) => el.scrollWidth <= el.clientWidth + 1), `the ${what} is not cut at ${width}px`).toBe(true)
      }
      await expect(chip).toHaveText('Fast to read, 25 characters a second')
      await expect(closed.locator('.review-speaker')).toBeHidden()
      await expect(closed.getByTestId('line-cps')).toBeHidden()
      await expect(closed.getByText(/^Shaky/)).toBeHidden()
      await expect(card(page, 24).locator('.review-speaker')).toHaveText('Wei Wuxian')
    }
    // The line only Whisper doubted keeps its score in the few words, as its badge is not shown.
    await expect(card(page, 7).getByTestId('queue-reason')).toHaveText('Whisper was unsure (50% sure)')
    await expectNoSidewaysScroll(page)
  })

  test('without This line the open card\'s whole sentence takes its own line, and the speaker keeps whole words', async ({ page }) => {
    seedCheckLines({ audio: true, real: true })
    await mockPeaks(page)
    await simple(page)
    await page.goto('/#/drama/3/review')
    const open = card(page, 24)
    await expect(open).toHaveAttribute('aria-current', 'true')
    for (const width of [1100, 1280]) {
      await page.setViewportSize({ width, height: 900 })
      const speaker = open.locator('.review-speaker')
      await expect(speaker).toHaveText('Wei Wuxian')
      const speakerLines = await speaker.evaluate((el) => {
        const r = document.createRange()
        r.selectNodeContents(el)
        return new Set([...r.getClientRects()].map((x) => Math.round(x.top))).size
      })
      expect(speakerLines, `the speaker's lines at ${width}px`).toBe(1)
      const sentence = open.getByTestId('queue-reason')
      await expect(sentence).toHaveText(GLOSSARY_SENTENCE)
      const [meta, s, box, more] = await Promise.all([open.locator('.review-line-meta'), sentence, open, open.getByRole('button', { name: 'More actions for line 24' })].map(async (l) => (await l.boundingBox())!))
      expect(s.y, `the sentence starts under the top line at ${width}px`).toBeGreaterThanOrEqual(meta.y + meta.height - 1)
      expect(s.width, `the sentence spans the card at ${width}px`).toBeGreaterThan(box.width * 0.85)
      expect(more.y + more.height, `More stays on the top line at ${width}px`).toBeLessThanOrEqual(meta.y + meta.height + 1)
      expect(box.x + box.width - (more.x + more.width), `More stays at the right at ${width}px`).toBeLessThan(40)
    }
  })

  test('picking another tools tab gives the open card its whole sentence and the lines around it; This line takes them back', async ({ page }) => {
    seedCheckLines({ audio: true, real: true })
    await mockPeaks(page)
    await page.setViewportSize({ width: 1440, height: 900 })
    await simple(page)
    await page.goto('/#/drama/3/review')
    const open = card(page, 24)
    await expect(page.getByRole('tab', { name: 'This line' })).toHaveAttribute('aria-selected', 'true')
    await expect(open.getByTestId('queue-reason')).toHaveText('Glossary name not used: 夷陵老祖')
    await expect(open.getByRole('button', { name: 'See the lines around it' })).toHaveCount(0)
    await page.getByRole('tab', { name: 'Look for problems' }).click()
    await expect(open.getByTestId('queue-reason')).toHaveText(GLOSSARY_SENTENCE)
    await expect(open.getByRole('button', { name: 'See the lines around it' })).toBeVisible()
    // The hidden panel is not drawn at all, so nothing in it runs.
    await expect(page.getByTestId('this-line')).toHaveCount(0)
    await page.getByRole('tab', { name: 'This line' }).click()
    await expect(open.getByTestId('queue-reason')).toHaveText('Glossary name not used: 夷陵老祖')
    await expect(open.getByRole('button', { name: 'See the lines around it' })).toHaveCount(0)
    await expect(page.getByTestId('this-line')).toBeVisible()
  })

  test('D opens the open card\'s details, F dismisses its flag and the card leaves; F does nothing on a line only Whisper doubted', async ({ page }) => {
    seedCheckLines()
    await simple(page)
    await page.goto('/#/drama/3/review')
    const open = card(page, 24)
    await expect(open).toHaveAttribute('aria-current', 'true')
    await open.locator('.review-line-meta').click()
    await open.focus()
    await page.keyboard.press('d')
    await expect(open.getByLabel('Sound cue (no dialogue)')).toBeVisible()
    await page.keyboard.press('Escape')
    await open.focus()
    await page.keyboard.press('f')
    await expect(page.locator('.review-line .review-idx')).toHaveText(['#7', '#12'])
    const saved = await (await page.request.get('/api/review/dramas/3/lines?page=1&page_size=40&only=all')).json()
    expect(saved.lines[23]).toMatchObject({ flag: null, reviewed: false })
    // #7 has no flag to dismiss: F leaves it listed.
    await card(page, 7).locator('.review-line-meta').click()
    await card(page, 7).focus()
    await page.keyboard.press('f')
    await expect(page.locator('.review-line .review-idx')).toHaveText(['#7', '#12'])
    await expect(card(page, 7)).toHaveAttribute('aria-current', 'true')
  })

  test('Space and Shift+Space never tick a queue card, which has no tick box; under All, Shift+Space still ticks', async ({ page }) => {
    seedCheckLines()
    await simple(page)
    await page.goto('/#/drama/3/review')
    const open = card(page, 24)
    await open.locator('.review-line-meta').click()
    await open.focus()
    // A key the browser keeps scrolls the page smoothly, so a scroll is caught by its event rather than read straight after.
    const scrollsAfter = async (key: string) => {
      const scrolled = page.evaluate(() => new Promise<boolean>((done) => {
        window.addEventListener('scroll', () => done(true), { once: true })
        setTimeout(() => done(false), 500)
      }))
      await page.keyboard.press(key)
      return scrolled
    }
    for (const key of [' ', 'Shift+ ']) {
      expect(await scrollsAfter(key), `the page stays put after ${key}`).toBe(false)
      await expect(page.getByTestId('selection-bar'), `no tick after ${key}`).toHaveCount(0)
      await expect(page.getByTestId('selection-count'), `no tick after ${key}`).toHaveCount(0)
    }
    await allRadio(page).check({ force: true })
    await card(page, 5).locator('.review-line-meta').click()
    await card(page, 5).focus()
    await page.keyboard.press('Shift+ ')
    await expect(page.getByTestId('selection-count')).toHaveText('1 selected')
  })

  test('under All the player is folded too: the lines come first, Player is shared with Need a look, and a failure unfolds it', async ({ page }) => {
    seedCheckLines({ audio: true, untranslated: true })
    await mockPeaks(page)
    await page.setViewportSize({ width: 1440, height: 900 })
    await simple(page)
    await expect(allRadio(page)).toBeChecked()
    const player = page.getByRole('group', { name: 'Player' })
    await expect(player).toBeHidden()
    await expect(card(page, 1)).toBeVisible()
    const first = (await card(page, 1).boundingBox())!
    expect(first.y + (await page.evaluate(() => window.scrollY)), 'the first line\'s top').toBeLessThan(420)
    const toggle = page.locator('.review-queue-row').getByRole('button', { name: 'Player', exact: true })
    // It names the strip it shows; the strip is hidden, so it is found by its class, not its role.
    const strip = page.locator('.review-player')
    await expect(toggle).toHaveAttribute('aria-controls', (await strip.getAttribute('id'))!)
    await toggle.click()
    await expect(player).toBeVisible()
    await page.reload()
    await expect(allRadio(page)).toBeChecked()
    await expect(player).toBeVisible()
    await queueRadio(page).check({ force: true })
    await expect(player).toBeVisible()
    await page.locator('.review-queue-row').getByRole('button', { name: 'Player', exact: true }).click()
    await expect(player).toBeHidden()
    await allRadio(page).check({ force: true })
    await expect(player).toBeHidden()

    // The sound fails to load: the player comes out with its error.
    await page.route('**/api/media/dramas/3/audio', (route) => route.fulfill({ status: 500, body: '' }))
    await page.goto('about:blank')
    await page.goto('/#/drama/3')
    await expect(player.getByRole('alert')).toHaveText(/^Couldn’t play the audio\./)
    await expect(player).toBeVisible()
    // The button reads as open, and one press folds the player again; the choice it remembers stays "folded".
    await expect(toggle).toHaveAttribute('aria-expanded', 'true')
    await toggle.click()
    await expect(player).toBeHidden()
    await expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(await page.evaluate(() => localStorage.getItem('baihe.pref.review.queue.media'))).toBe('false')
    // Play on a line still fails, and brings the folded player out again with its error.
    await card(page, 5).locator('.review-line-meta').click()
    await card(page, 5).getByRole('button', { name: '▶ Play' }).click()
    await expect(player).toBeVisible()
    await expect(player.getByRole('alert')).toHaveText(/^Couldn’t play the audio\./)
    await expect(toggle).toHaveAttribute('aria-expanded', 'true')
  })

  test.describe('on title 1, with its long name', () => {
    test.afterAll(() => clearCheckLines(1))

    test('the header is one row at 1440 with the menu docked: the kind under the name, the controls beside it', async ({ page }) => {
      seedCheckLines({ drama: 1 })
      await page.addInitScript(() => localStorage.setItem('baihe.navDock', 'docked'))
      await page.setViewportSize({ width: 1440, height: 900 })
      await page.goto('/#/drama/1')
      await page.getByRole('radio', { name: 'Simple', exact: true }).check({ force: true })
      const header = page.locator('.workspace-header')
      const name = page.getByTestId('drama-title')
      await expect(name).toHaveText('Grandmaster of Demonic Cultivation')
      await expect(header.getByTestId('title-kind')).toHaveText('Audio drama · Chinese to English')
      const controls = page.locator('.title-top-actions')
      await expect(controls.getByRole('link')).toHaveText(['Add media', 'Redo transcription', 'Glossary'])
      const [n, c, h] = [(await name.boundingBox())!, (await controls.boundingBox())!, (await header.boundingBox())!]
      expect(c.y < n.y + n.height && n.y < c.y + c.height, 'the name and the controls share a row').toBe(true)
      expect(h.height, 'the header\'s height').toBeLessThan(70)
      await page.setViewportSize({ width: 1100, height: 800 })
      const cut = await controls.evaluate((row) => [...row.querySelectorAll('a, button, label')].filter((el) => el.scrollWidth > el.clientWidth + 1).map((el) => el.textContent))
      expect(cut, 'controls cut at 1100px').toEqual([])
      for (const name of ['Add media', 'Redo transcription', 'Glossary']) await expect(controls.getByRole('link', { name, exact: true })).toBeInViewport()
      await expect(controls.getByRole('button', { name: 'More', exact: true })).toBeInViewport()
      await expectNoSidewaysScroll(page)
    })
  })

  test.describe('in a series', () => {
    test.afterAll(() => leaveSeries())

    test('the Episodes tab ends the tools, lists the series with this title marked, is never remembered, and is not in Full', async ({ page }) => {
      seedCheckLines({ audio: true })
      joinSeries()
      await mockPeaks(page)
      await page.setViewportSize({ width: 1440, height: 900 })
      await simple(page)
      const tablist = page.getByRole('tablist', { name: 'Review tools' })
      await expect(tablist.getByRole('tab')).toHaveText(['This line', 'Look for problems', 'Change many lines', 'Finish', 'Episodes'])
      // The row wraps rather than cutting a label, in both styles.
      for (const style of ['frost', 'soft']) {
        await page.evaluate((s) => (s === 'soft' ? document.documentElement.setAttribute('data-style', s) : document.documentElement.removeAttribute('data-style')), style)
        const cut = await tablist.evaluate((row) => {
          const box = row.getBoundingClientRect()
          return [...row.querySelectorAll('[role="tab"]')].filter((t) => {
            const r = t.getBoundingClientRect()
            return t.scrollWidth > t.clientWidth + 1 || r.left < box.left - 1 || r.right > box.right + 1
          }).map((t) => t.textContent)
        })
        expect(cut, `tabs cut in ${style}`).toEqual([])
        // Two rows of the column, not three.
        const tops = await tablist.getByRole('tab').evaluateAll((tabs) => tabs.map((t) => Math.round(t.getBoundingClientRect().top)))
        expect(new Set(tops).size, `tab rows in ${style}: ${tops}`).toBeLessThanOrEqual(2)
        expect((await tablist.boundingBox())!.height, `the tab row's height in ${style}`).toBeLessThanOrEqual(2 * 44 + 8 + 2)
      }

      await tablist.getByRole('tab', { name: 'Change many lines' }).click()
      await tablist.getByRole('tab', { name: 'Episodes' }).click()
      await expect(tablist.getByRole('tab', { name: 'Episodes' })).toHaveAttribute('aria-selected', 'true')
      const list = page.getByRole('list', { name: 'Episodes' })
      await expect(list.getByRole('listitem')).toHaveCount(2)
      await expect(list.getByRole('link', { name: /Signal/ })).toHaveAttribute('aria-current', 'page')
      await expect(list.getByRole('link', { name: /Grandmaster/ })).toHaveAttribute('href', '#/drama/1')
      await expect(list.getByRole('link', { name: /Grandmaster/ })).not.toHaveAttribute('aria-current', 'page')
      // The open title is marked to the eye too: its name bold, its row on a tint the other rows do not have.
      const look = (name: RegExp) => list.getByRole('link', { name }).evaluate((row) => [getComputedStyle(row).backgroundColor, getComputedStyle(row.querySelector('.chapters-title')!).fontWeight])
      const [[openFill, openWeight], [otherFill, otherWeight]] = [await look(/Signal/), await look(/Grandmaster/)]
      expect(openFill).not.toBe(otherFill)
      expect(Number(openWeight)).toBeGreaterThan(Number(otherWeight))
      expect(await page.evaluate(() => localStorage.getItem('baihe.pref.review.tools.tab.3'))).toBe('"change"')
      // A group picked after it opens as before; This line opens over it when a card opens anew.
      await tablist.getByRole('tab', { name: 'Finish' }).click()
      await expect(tablist.getByRole('tab', { name: 'Finish' })).toHaveAttribute('aria-selected', 'true')
      await tablist.getByRole('tab', { name: 'Episodes' }).click()
      await allRadio(page).check({ force: true })
      await queueRadio(page).check({ force: true })
      await expect(tablist.getByRole('tab', { name: 'This line' })).toHaveAttribute('aria-selected', 'true')
      // No ?tools= link opens it.
      await page.goto('about:blank')
      await page.goto('/#/drama/3/review?tools=episodes')
      await expect(tablist.getByRole('tab', { name: 'Episodes' })).toHaveAttribute('aria-selected', 'false')

      await page.getByRole('radio', { name: 'Full', exact: true }).check({ force: true })
      await expect(page.getByRole('navigation', { name: 'Stages' })).toBeVisible()
      await expect(tablist.getByRole('tab').first()).toBeVisible()
      await expect(tablist.getByRole('tab', { name: 'Episodes' })).toHaveCount(0)
    })
  })
})
