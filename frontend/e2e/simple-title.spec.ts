import { GLOSSARY_SENTENCE, seedCheckLines, seedLines } from './compareSelectedHelpers'
import { clearLines } from './resegmentLlmMocks'
import type { Page } from '@playwright/test'

import { expect, test } from './fixtures'

// Audio and video titles have a Simple view (#/drama/1, no stage) beside today's workspace; the choice is
// remembered in the browser. Title 1 is the seeded audio drama, 3 the seeded video, 2 the novel.

const progress = (id: number, lines: number, untranslated: number) => ({
  drama_id: id, stage_index: 1, stage: 'translate', line_count: lines, untranslated_count: untranslated,
  flagged_count: 0, has_audio: false, has_dub_track: false, exported: false, stages: [],
})

test('Simple shows the five actions and the episode, and Full brings the stage tabs back', async ({ page }) => {
  await page.route('**/api/workflow/dramas/1/progress', (route) => route.fulfill({ json: progress(1, 10, 10) }))
  await page.goto('/#/drama/1')
  await expect(page.getByRole('navigation', { name: 'Stages' })).toBeVisible()
  await page.getByRole('radio', { name: 'Simple', exact: true }).check({ force: true })
  await expect(page).toHaveURL(/#\/drama\/1$/)
  await expect(page.getByRole('navigation', { name: 'Stages' })).toHaveCount(0)
  // The step tabs open each stage; the current step's action is the main button, and Add media, Redo and Glossary stay beside it.
  const tabs = page.getByRole('list', { name: 'Steps' })
  await expect(tabs.getByRole('link', { name: /^Add/ })).toHaveAttribute('href', '#/drama/1/source?tab=add')
  await expect(tabs.getByRole('link', { name: /^Transcribe/ })).toHaveAttribute('href', '#/drama/1/source?tab=transcribe')
  await expect(tabs.getByRole('link', { name: /^Translate/ })).toHaveAttribute('href', '#/drama/1/translate')
  await expect(tabs.getByRole('link', { name: /^Check/ })).toHaveAttribute('href', '#/drama/1/review')
  await expect(tabs.getByRole('link', { name: /^Export/ })).toHaveAttribute('href', '#/drama/1/export')
  await expect(page.getByRole('link', { name: 'Translate 10 lines', exact: true })).toHaveAttribute('href', '#/drama/1/translate')
  await expect(page.getByRole('link', { name: 'Redo transcription' })).toHaveAttribute('href', '#/drama/1/source?tab=transcribe')
  await expect(page.getByRole('link', { name: 'Add media', exact: true })).toHaveAttribute('href', '#/drama/1/source?tab=add')
  await expect(page.getByRole('link', { name: 'Glossary' })).toHaveAttribute('href', '#/drama/1/translate?tab=glossary')
  await page.getByRole('tab', { name: 'Episodes' }).click()
  const episodes = page.getByRole('list', { name: 'Episodes' })
  await expect(episodes.getByRole('listitem')).toHaveCount(1)
  await expect(episodes).toContainText('Transcribed · Not translated')
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
  await expect(page.getByRole('list', { name: 'Steps' }).getByRole('link', { name: /^Transcribe/ })).toHaveAttribute('href', '#/drama/3/source?tab=transcribe')
  await page.getByRole('tab', { name: 'Episodes' }).click()
  await expect(page.getByRole('list', { name: 'Episodes' })).toContainText('Not transcribed')
  await page.reload()
  await expect(page.getByRole('list', { name: 'Steps' })).toBeVisible()
  await page.goto('/#/drama/3/review')
  // The stage opens on a slim page: no stage tabs, a way back to the title page, and Full brings the tabs back.
  await expect(page.getByRole('link', { name: 'Back to title page' })).toHaveAttribute('href', '#/drama/3')
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
  await page.getByRole('list', { name: 'Steps' }).getByRole('link', { name: /^Export/ }).click()
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
  await expect(steps.locator('[aria-current="step"]')).toContainText('Translate')
  await expect(steps.locator('[aria-current="step"]')).toContainText('4 of 10 lines left')
  await expect(steps).toContainText('10 lines')
})

test('with lines the title opens on a progress bar and the first lines, with the step tabs above them', async ({ page }) => {
  const line = (id: number, zh: string, en: string) => ({ id, idx: id, zh, en, start: id, end: id + 1, speaker: null, reviewed: false })
  await page.route('**/api/workflow/dramas/1/progress', (route) => route.fulfill({ json: progress(1, 10, 4) }))
  await page.route('**/api/review/dramas/1/lines?*', (route) => route.fulfill({
    json: { total: 10, page: 1, page_size: 8, lines: [line(1, '你好，今天过得怎么样？', 'Hello, how was your day?'), line(2, '还没翻译的一行', '')] },
  }))
  await page.goto('/#/drama/1')
  await page.getByRole('radio', { name: 'Simple', exact: true }).check({ force: true })
  const bar = page.getByRole('progressbar', { name: 'Lines translated' })
  await expect(bar).toHaveAttribute('aria-valuenow', '6')
  await expect(page.getByText('6 of 10 lines translated')).toBeVisible()
  const lines = page.getByRole('region', { name: 'First lines' })
  await expect(lines.getByText('Hello, how was your day?')).toBeVisible()
  await expect(lines.getByText('Not translated yet')).toBeVisible()
  // All lines, not the check queue Review opens on in Simple.
  await expect(lines.getByRole('link', { name: 'Open all 10 lines' })).toHaveAttribute('href', '#/drama/1/review?focus=all')

  // Wide window: the step tabs run across the top, above the bar and the lines.
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
  await expect(steps.getByRole('link', { name: /^Check/ })).toHaveAttribute('href', '#/drama/1/review')
  await expect(page.getByRole('navigation', { name: 'Title steps' })).toHaveCount(0)
  await expect(page.getByRole('navigation', { name: 'Stages' })).toHaveCount(0)
  await steps.getByRole('link', { name: /^Check/ }).click()
  await expect(page).toHaveURL(/#\/drama\/1\/review$/)
  await expect(page.getByRole('list', { name: 'Steps' })).toBeVisible()
  await page.getByRole('link', { name: 'Back to title page' }).click()
  await expect(page).toHaveURL(/#\/drama\/1$/)
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

test('the title panel has Translate, Glossary, Episodes and Export, and a Video tab only when there is a source video', async ({ page }) => {
  const status = (video: boolean) => ({ has_audio: video, has_source_video: video })
  await page.route('**/api/workflow/dramas/1/progress', (route) => route.fulfill({ json: progress(1, 10, 4) }))
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
    await expect(card(page, 7).getByTestId('queue-reason')).toHaveText('Whisper was unsure')
    await expect(card(page, 12).getByTestId('queue-reason')).toHaveText('Fast to read, 25 characters a second')
    await expect(page.locator('.review-line').getByTestId('line-flag')).toHaveCount(0)
    await expect(card(page, 24).getByRole('link', { name: 'Open glossary' })).toHaveAttribute('href', '#/drama/3/translate?tab=glossary')
    // The English under its label, boxed like a field; the buttons in the drawing's order, with the green finishing step last.
    await expect(card(page, 24).locator('.review-queue-label')).toHaveText('English')
    const box = card(page, 24).getByTestId('line-en')
    expect(await box.evaluate((el) => getComputedStyle(el).borderTopStyle)).toBe('solid')
    await expect(card(page, 24).getByRole('toolbar').getByRole('button').or(card(page, 24).getByRole('toolbar').getByRole('link')))
      .toHaveText(['Open glossary', 'Improve translation', 'Edit details', 'Dismiss flag', 'Looks right, next'])
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
    await expect(page.getByRole('link', { name: 'Check the 3 lines', exact: true })).toHaveAttribute('href', '#/drama/3/review')
    await page.getByRole('link', { name: 'Check the 3 lines', exact: true }).click()
    await expect(queueRadio(page)).toBeChecked()
    // On the Review page the same button opens the queue there.
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
    await expect(page.getByRole('list', { name: 'Steps' }).locator('[aria-current="step"]')).toContainText('Nothing needs a look')
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
})

