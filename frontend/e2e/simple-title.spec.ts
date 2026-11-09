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
  await expect(page.getByRole('link', { name: 'Translate', exact: true })).toHaveAttribute('href', '#/drama/1/translate')
  await expect(page.getByRole('link', { name: 'Redo' })).toHaveAttribute('href', '#/drama/1/source?tab=transcribe')
  await expect(page.getByRole('link', { name: 'Add media' })).toHaveAttribute('href', '#/drama/1/source?tab=add')
  await expect(page.getByRole('link', { name: 'Glossary' })).toHaveAttribute('href', '#/drama/1/translate?tab=glossary')
  await expect(page.getByRole('link', { name: 'Review' })).toHaveAttribute('href', '#/drama/1/review')
  await expect(page.getByRole('link', { name: 'Export' })).toHaveAttribute('href', '#/drama/1/export')
  const episodes = page.getByRole('list', { name: 'Episodes' })
  await expect(episodes.getByRole('listitem')).toHaveCount(1)
  await expect(episodes).toContainText('Transcribed · Not translated')
  await page.getByRole('radio', { name: 'Full', exact: true }).check({ force: true })
  await expect(page.getByRole('navigation', { name: 'Stages' })).toBeVisible()
})

test('the choice is remembered, and a link that names a stage still opens that stage', async ({ page }) => {
  await page.route('**/api/workflow/dramas/3/progress', (route) => route.fulfill({ json: progress(3, 0, 0) }))
  // Without audio the button stays a link to Transcribe; the seeded video may or may not have audio, so pin it.
  await page.route('**/api/transcribe/dramas/3/config', async (route) => {
    const resp = await route.fetch()
    await route.fulfill({ response: resp, json: { ...(await resp.json()), audio_available: false } })
  })
  await page.goto('/#/drama/3')
  await page.getByRole('radio', { name: 'Simple', exact: true }).check({ force: true })
  await expect(page.getByRole('link', { name: 'Transcribe and translate' })).toHaveAttribute('href', '#/drama/3/source?tab=transcribe')
  await expect(page.getByRole('list', { name: 'Episodes' })).toContainText('Not transcribed')
  await page.reload()
  await expect(page.getByRole('link', { name: 'Transcribe and translate' })).toBeVisible()
  await page.goto('/#/drama/3/review')
  // The stage opens on a slim page: no stage tabs, a way back to the title page, and Full brings the tabs back.
  await expect(page.getByRole('link', { name: 'Back to title page' })).toHaveAttribute('href', '#/drama/3')
  await expect(page.getByRole('navigation', { name: 'Stages' })).toHaveCount(0)
  await page.getByRole('radio', { name: 'Full', exact: true }).check({ force: true })
  await expect(page.getByRole('navigation', { name: 'Stages' })).toBeVisible()
})

test('Simple leaves the advanced controls out of the stages, and Full brings them back', async ({ page }) => {
  await page.route('**/api/workflow/dramas/1/progress', (route) => route.fulfill({ json: progress(1, 10, 10) }))
  await page.goto('/#/drama/1')
  await page.getByRole('radio', { name: 'Simple', exact: true }).check({ force: true })
  await page.getByRole('link', { name: 'Export' }).click()
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
