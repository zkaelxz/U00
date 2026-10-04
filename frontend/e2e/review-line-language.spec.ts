import { expect, test, type Page, type Route } from '@playwright/test'

import { hitHeight, installHitArea } from './hitArea'

// Review: a line's own spoken language. The chip shows only for a line in
// another language than the drama's (seeded drama 3 is Korean), Edit details
// sets one line's language and the line sheet's "Set language…" sets it for
// one line or all of a speaker's lines. Lines and every write are mocked (any
// other write is aborted), so the shared library is never touched.

type MockLine = {
  id: number; idx: number; start: number; end: number; zh: string; en: string; speaker: string | null
  speaker_manual: boolean; sfx: boolean; flag: null; flag_note: null; dub_filename: null; lang: string | null
}

const line = (id: number, speaker: string | null, lang: string | null): MockLine => ({
  id, idx: id - 1, start: id, end: id + 1, zh: `句${id}`, en: `Line ${id}`, speaker,
  speaker_manual: false, sfx: false, flag: null, flag_note: null, dub_filename: null, lang,
})

async function mockLines(page: Page) {
  const lines = [line(1, 'Mina', null), line(2, 'Jun', 'ja'), line(3, 'Mina', 'ko')]
  const posts: { path: string; body: Record<string, unknown> }[] = []
  const unmocked: string[] = []
  const json = (route: Route, body: unknown) => route.fulfill({ json: body })
  const body = (route: Route) => JSON.parse(route.request().postData() ?? '{}') as Record<string, unknown>

  await page.route('**/api/**', (route) => {
    const r = route.request()
    if (r.method() === 'GET') return route.continue()
    unmocked.push(`${r.method()} ${r.url()}`)
    return route.abort()
  })
  await page.route('**/api/review/dramas/3/lines?*', (route) =>
    json(route, { lines, page: 1, page_size: 40, total: lines.length, flagged_count: 0, untranslated_count: 0 }))
  await page.route('**/api/lines/dramas/3/lines/*', (route) => {
    const b = body(route)
    posts.push({ path: new URL(route.request().url()).pathname, body: b })
    const target = lines.find((l) => route.request().url().endsWith(`/lines/${l.id}`))!
    if (typeof b.lang === 'string') target.lang = b.lang || null
    return json(route, target)
  })
  await page.route('**/api/lines/dramas/3/set-language', (route) => {
    const b = body(route)
    posts.push({ path: '/set-language', body: b })
    const chosen = lines.filter((l) => (b.speaker ? l.speaker === b.speaker : (b.line_ids as number[]).includes(l.id)))
    const changed = chosen.filter((l) => l.lang !== (b.lang || null))
    changed.forEach((l) => (l.lang = (b.lang as string | null) || null))
    return json(route, { updated: changed.length, line_ids: chosen.map((l) => l.id), skipped_ids: [] })
  })
  return { posts, unmocked }
}

const row = (page: Page, id: number) => page.locator(`.review-line[data-line-id="${id}"]`)

async function open(page: Page) {
  await page.goto('/#/drama/3/review')
  await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(3)
}

async function openLanguageSheet(page: Page, id: number) {
  await row(page, id).getByRole('button', { name: `More actions for line ${id}` }).click()
  await page.getByRole('button', { name: 'Set language…' }).click()
  await expect(page.getByRole('heading', { name: `Spoken language of #${id}` })).toBeVisible()
}

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

test('desktop: chip only for another language; edit one line; set a speaker’s lines', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  const m = await mockLines(page)
  await open(page)
  await expect(row(page, 2).getByTestId('line-lang')).toHaveText('JA Spoken in Japanese')
  // No lang, and Korean on a Korean drama: nothing new on the row.
  await expect(row(page, 1).getByTestId('line-lang')).toHaveCount(0)
  await expect(row(page, 3).getByTestId('line-lang')).toHaveCount(0)

  await row(page, 1).getByTestId('line-en').click()
  await row(page, 1).getByRole('button', { name: 'Edit details' }).click()
  const select = row(page, 1).getByLabel('Spoken language')
  await expect(select.locator('option').first()).toHaveText('Title default (Korean)')
  expect((await select.boundingBox())!.height).toBeGreaterThanOrEqual(32)
  await select.selectOption('en')
  await row(page, 1).getByRole('button', { name: 'Save', exact: true }).click()
  await expect(row(page, 1).getByTestId('line-lang')).toContainText('EN')
  expect(m.posts).toEqual([{ path: '/api/lines/dramas/3/lines/1', body: { lang: 'en', expected: { lang: '' } } }])

  const more = row(page, 3).getByRole('button', { name: 'More actions for line 3' })
  expect(await hitHeight(more)).toBeGreaterThanOrEqual(32)
  await openLanguageSheet(page, 3)
  await page.getByLabel('Spoken language', { exact: true }).selectOption('ja')
  await page.getByRole('radio', { name: 'All lines by Mina' }).check()
  await page.getByRole('button', { name: 'Set language', exact: true }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Set 2 lines to Japanese.' })).toBeVisible()
  expect(m.posts.at(-1)).toEqual({ path: '/set-language', body: { lang: 'ja', speaker: 'Mina' } })
  await expect(row(page, 1).getByTestId('line-lang')).toContainText('JA')
  await expect(row(page, 3).getByTestId('line-lang')).toContainText('JA')
  if (process.env.SHOT_DIR) await page.locator('.review-list, ul:has(> .review-line)').first().screenshot({ path: `${process.env.SHOT_DIR}/line-language-desktop.png` })
  expect(m.unmocked).toEqual([])
})

test.describe('phone', () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true, deviceScaleFactor: 3 })

  test('set one line back to the title default; 44px targets, no sideways scroll', async ({ page }) => {
    const m = await mockLines(page)
    await open(page)
    await expect(row(page, 2).getByTestId('line-lang')).toContainText('JA')
    await openLanguageSheet(page, 2)
    const select = page.getByLabel('Spoken language', { exact: true })
    await expect(select).toHaveValue('ja')
    const targets = [select, page.getByRole('button', { name: 'Set language', exact: true }),
      page.getByRole('button', { name: 'Back' }),
      page.locator('.review-lang-scope label').first(), page.locator('.review-lang-scope label').last()]
    for (const t of targets) expect(await hitHeight(t)).toBeGreaterThanOrEqual(44)
    const { scroll, client } = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth,
    }))
    expect(scroll).toBeLessThanOrEqual(client)
    if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/line-language-phone.png` })

    await select.selectOption('')
    await page.getByRole('button', { name: 'Set language', exact: true }).click()
    await expect(page.getByRole('status').filter({ hasText: 'Set 1 line to the title default (Korean).' })).toBeVisible()
    expect(m.posts).toEqual([{ path: '/set-language', body: { lang: null, line_ids: [2] } }])
    await expect(row(page, 2).getByTestId('line-lang')).toHaveCount(0)
    expect(m.unmocked).toEqual([])
  })
})
