import type { Page } from '@playwright/test'

import { expect, test } from './fixtures'

// The Series page (#/series/<id>): titles, copy glossary terms from another
// series, rename and delete. /api/series is mocked so the shared seeded
// library is never changed; everything else is the seeded API.

const SERIES = [
  { id: 9, name: 'Saga', is_private: false, owned_by_me: true, title_count: 2, glossary_term_count: 1 },
  { id: 12, name: 'Other', is_private: false, owned_by_me: true, title_count: 1, glossary_term_count: 3 },
]

const FIELDS = [
  { key: 'translation_engine', label: 'Translation engine', group: 'translate', type: 'choice', choices: ['claude', 'gemini'] },
  { key: 'default_female_pronouns', label: 'Default to she/her', group: 'translate', type: 'bool' },
  { key: 'beam_size', label: 'Beam size', group: 'transcribe', type: 'int', min: 1, max: 10 },
  { key: 'vad_threshold', label: 'Voice detection threshold', group: 'transcribe', type: 'number', min: 0.1, max: 0.9 },
]

const PAGE = {
  id: 9, name: 'Saga', is_private: false, owned_by_me: true, can_edit: true, has_instructions: true,
  glossary_term_count: 1, character_count: 2,
  titles: [
    { id: 1, title_en: 'Saga Ep 1', title_zh: '传奇一', media_type: 'audio_drama', status: 'translated', episode_number: 1, line_count: 120 },
    { id: 2, title_en: 'Saga Ep 2 with a very long title that has to wrap on a phone screen', title_zh: null, media_type: 'novel', status: null, episode_number: 2, line_count: 1 },
  ],
}

async function mockSeries(page: Page) {
  const posts: { path: string; body: unknown }[] = []
  await page.route('**/api/series**', async (route) => {
    const req = route.request()
    const path = new URL(req.url()).pathname
    if (req.method() === 'POST') {
      const body = req.postDataJSON()
      posts.push({ path, body })
      if (path.endsWith('/rename')) return route.fulfill({ json: { id: 9, name: body.name } })
      if (path.endsWith('/copy-terms')) return route.fulfill({ json: { series_id: 9, source_series_id: 12, added: 2, skipped: 1 } })
      if (path.endsWith('/translation-memory/import')) return route.fulfill({ json: { series_id: 9, added: 1, skipped: 0 } })
      if (path.endsWith('/defaults')) return route.fulfill({ json: { series_id: 9, settings: body.settings, fields: FIELDS } })
      if (path.endsWith('/defaults/apply')) {
        return route.fulfill({ json: { series_id: 9, mode: body.mode, title_count: 2, titles_changed: 1, fields_changed: 2 } })
      }
      if (path.endsWith('/delete')) {
        return route.fulfill({ json: { deleted: true, series_id: 9, titles_kept: 2, glossary_terms_deleted: 1, characters_deleted: 2 } })
      }
    }
    if (path === '/api/series') return route.fulfill({ json: { items: SERIES } })
    if (path === '/api/series/9') return route.fulfill({ json: PAGE })
    if (path === '/api/series/9/copy-terms-preview') {
      return route.fulfill({
        json: { series_id: 9, source_series_id: 12, new_count: 2, skipped_count: 1, new_terms: ['林峰', '青云'], skipped_terms: ['苏'] },
      })
    }
    if (path === '/api/series/9/translation-memory.tmx') {
      return route.fulfill({ contentType: 'application/x-tmx+xml', body: '<tmx version="1.4"><body/></tmx>' })
    }
    if (path === '/api/series/9/defaults') {
      return route.fulfill({ json: { series_id: 9, settings: { default_female_pronouns: true }, fields: FIELDS } })
    }
    if (path === '/api/series/9/defaults/apply-preview') {
      return route.fulfill({
        json: {
          series_id: 9, mode: new URL(req.url()).searchParams.get('mode'), title_count: 2, changed_titles: 1, fields_changed: 2, job_running: false,
          titles: [
            { id: 1, title_en: 'Saga Ep 1', title_zh: null, changes: { beam_size: { from: null, to: 8 }, default_female_pronouns: { from: false, to: true } }, unsuited: [] },
            { id: 2, title_en: 'Saga Ep 2', title_zh: null, changes: {}, unsuited: [] },
          ],
        },
      })
    }
    if (path === '/api/series/9/delete-preview') {
      return route.fulfill({
        json: {
          series_id: 9, name: 'Saga', title_count: 2, glossary_term_count: 1, character_count: 2,
          translation_memory_count: 0, dismissal_count: 0, style_profile_count: 1, has_instructions: true, needs_typed_confirm: true, job_running: false,
        },
      })
    }
    return route.fulfill({ status: 404, json: { error: { code: 'not_found', message: 'Not found.' } } })
  })
  return posts
}

async function noSidewaysScroll(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

test('opens a series from the Library and uses every tab', async ({ page }) => {
  const posts = await mockSeries(page)
  await page.goto('/')
  await page.getByText('Series (2)').click()
  await page.getByRole('link', { name: 'Saga', exact: true }).click()
  await expect(page).toHaveURL(/#\/series\/9$/)
  await expect(page.getByRole('heading', { name: 'Saga' })).toBeVisible()
  await expect(page.getByTestId('series-meta')).toHaveText('2 titles · 1 glossary term · 2 series characters')

  const titles = page.getByTestId('series-titles')
  await expect(titles.getByRole('link', { name: /Saga Ep 1/ })).toHaveAttribute('href', '#/drama/1')
  await expect(titles).toContainText('120 lines')
  await expect(titles).toContainText('1 line')

  await page.getByRole('tab', { name: 'Glossary tools' }).click()
  await expect(page).toHaveURL(/#\/series\/9\?tab=glossary$/)
  await expect(page.getByRole('link', { name: 'Open glossary and instructions' })).toHaveAttribute('href', '#/drama/1/translate?tab=glossary')
  await expect(page.getByTestId('series-no-series-hint')).toContainText('New series…')
  const copy = page.locator('.series-copy-card')
  await copy.getByLabel('Copy from').selectOption({ label: 'Other (3)' })
  await copy.getByRole('button', { name: 'Preview' }).click()
  await expect(copy.getByTestId('series-copy-preview')).toContainText('2 new terms will be added, 1 is already there and is skipped.')
  await copy.getByRole('button', { name: 'Copy 2 terms' }).click()
  await expect(copy.getByTestId('series-copy-result')).toHaveText('Added 2 terms; skipped 1 already there.')

  await page.getByRole('tab', { name: 'Settings' }).click()
  const name = page.getByLabel('Series name')
  await name.fill(' OTHER ')
  await expect(page.getByText('There is already a series called “Other”.')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Rename series' })).toBeDisabled()
  await name.fill('Saga II')
  await page.getByRole('button', { name: 'Rename series' }).click()

  await page.getByRole('button', { name: 'Delete series…' }).click()
  const prompt = page.getByTestId('series-delete-prompt')
  await expect(prompt).toContainText('2 titles kept: they become “No series”.')
  await expect(prompt).toContainText('1 glossary term')
  await expect(prompt).toContainText('the series instructions')
  await expect(prompt).toContainText('the learned style profile')
  await expect(prompt.getByRole('button', { name: 'Delete series' })).toBeDisabled()
  await prompt.getByLabel('Type DELETE to confirm').fill('DELETE')
  await prompt.getByRole('button', { name: 'Delete series' }).click()
  await expect(page).toHaveURL(/#\/library$/)

  expect(posts).toEqual([
    { path: '/api/series/9/copy-terms', body: { source_series_id: 12 } },
    { path: '/api/series/9/rename', body: { name: 'Saga II' } },
    { path: '/api/series/9/delete', body: { confirm: true, confirm_text: 'DELETE' } },
  ])
})

test('Glossary tools: export the translation memory as TMX and import a TMX file', async ({ page }) => {
  const posts = await mockSeries(page)
  await page.goto('/#/series/9?tab=glossary')
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Export TMX' }).click()
  expect((await download).suggestedFilename()).toBe('series-9-memory.tmx')
  await page.getByTestId('series-tmx-file').setInputFiles({
    name: 'memory.tmx', mimeType: 'application/xml', buffer: Buffer.from('<tmx version="1.4"><body/></tmx>'),
  })
  await expect.poll(() => posts.find((p) => p.path.endsWith('/translation-memory/import'))?.body).toEqual({ tmx: '<tmx version="1.4"><body/></tmx>' })
})

test('Defaults: save title settings, preview and apply them to every title', async ({ page }) => {
  const posts = await mockSeries(page)
  await page.goto('/#/series/9?tab=defaults')
  const card = page.locator('.series-defaults-card')
  await expect(card.getByLabel('Default to she/her')).toHaveValue('true')
  await card.getByLabel('Beam size').fill('99')
  await card.getByRole('button', { name: 'Save defaults' }).click()
  await expect(card.getByRole('alert')).toHaveText('Beam size: between 1 and 10.')
  await card.getByLabel('Beam size').fill('8')
  await card.getByLabel('Translation engine').selectOption('gemini')
  await card.getByRole('button', { name: 'Save defaults' }).click()
  await expect(card.getByTestId('series-defaults-saved')).toBeVisible()

  const apply = page.locator('.series-defaults-apply')
  await apply.getByLabel("Overwrite every title's settings").check()
  await apply.getByRole('button', { name: 'Preview' }).click()
  const preview = apply.getByTestId('series-defaults-preview')
  await expect(preview).toContainText('1 title of 2 would change (2 settings in all).')
  await expect(preview).toContainText('Beam size app default → 8')
  await expect(preview).not.toContainText('Saga Ep 2')
  await apply.getByRole('button', { name: 'Apply to 1 title' }).click()
  await expect(apply.getByTestId('series-defaults-result')).toHaveText('Changed 2 settings on 1 title.')

  expect(posts).toEqual([
    { path: '/api/series/9/defaults', body: { settings: { translation_engine: 'gemini', default_female_pronouns: true, beam_size: 8 } } },
    { path: '/api/series/9/defaults/apply', body: { mode: 'overwrite' } },
  ])
})

test.describe('phone', () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true })

  test('every tab fits a 390px screen', async ({ page }) => {
    await mockSeries(page)
    await page.goto('/#/series/9')
    await expect(page.getByTestId('series-titles')).toContainText('very long title')
    await noSidewaysScroll(page)
    for (const tab of ['Defaults', 'Glossary tools', 'Settings']) {
      await page.getByRole('tab', { name: tab }).tap()
      await expect(page.getByRole('tab', { name: tab })).toHaveAttribute('aria-selected', 'true')
      await noSidewaysScroll(page)
    }
    await page.getByRole('button', { name: 'Delete series…' }).tap()
    await expect(page.getByTestId('series-delete-prompt')).toBeVisible()
    await noSidewaysScroll(page)
  })
})

test('sorts a series\' titles by date, defaults oldest first, and remembers the choice per series', async ({ page }) => {
  const dated = {
    ...PAGE,
    titles: [
      { ...PAGE.titles[0], id: 1, title_en: 'Bravo', release_date: '2025-02-01' },
      { ...PAGE.titles[1], id: 2, title_en: 'Alpha', release_date: null },
      { ...PAGE.titles[0], id: 3, title_en: 'Charlie', release_date: '2024-01-01' },
    ],
  }
  await mockSeries(page)
  await page.route('**/api/series/9', (r) => r.fulfill({ json: dated }))
  await page.goto('/#/series/9')
  const names = () => page.locator('.series-titles .table-title').allTextContents()
  const sort = page.getByLabel('Sort titles by')
  // Some title has a date: oldest first, the undated one last.
  await expect(sort).toHaveValue('date-asc')
  expect((await names()).map((n) => n.replace(/^#\d+ /, ''))).toEqual(['Charlie', 'Bravo', 'Alpha'])
  await expect(page.getByTestId('series-titles')).toContainText('2024-01-01')
  await sort.selectOption('date-desc')
  expect((await names()).map((n) => n.replace(/^#\d+ /, ''))).toEqual(['Bravo', 'Charlie', 'Alpha'])
  await sort.selectOption('title')
  expect((await names()).map((n) => n.replace(/^#\d+ /, ''))).toEqual(['Alpha', 'Bravo', 'Charlie'])
  await page.reload()
  await expect(page.getByLabel('Sort titles by')).toHaveValue('title')
})

test('a series with no dates keeps its current order by default', async ({ page }) => {
  await mockSeries(page)
  await page.goto('/#/series/9')
  await expect(page.getByLabel('Sort titles by')).toHaveValue('added')
})
