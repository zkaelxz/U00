import { expect, test, type Page, type Route } from '@playwright/test'

import { ME } from './authMocks'

// Translate > Characters extras (inventory C02, C04, C07, C08): voice-match
// suggestions (accept/reject), sample lines and the "shared with series"
// caption, custom pronouns with the series default, and "remember in this
// series". The drama read hits the real seeded API (reported as series 7);
// every characters route is mocked and any unmocked write is aborted and
// recorded, so the shared library is never written.

test.use({ viewport: { width: 1280, height: 800 } })

const SHOTS = process.env.SHOT_DIR

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true })
}

async function openSection(page: Page, title: string) {
  const summary = page.locator('summary').filter({ has: page.locator('.section-title', { hasText: new RegExp(`^${title}$`) }) }).first()
  if ((await summary.locator('xpath=..').getAttribute('open')) === null) await summary.click()
}

const entry = (over: object = {}) => ({
  speaker_label: 'SPEAKER_00', character_name: '', voice_actor: '', pronouns: '',
  clone_engine: '', voice_design: '', has_ref_audio: false, ref_text_present: false,
  series_character_id: null as number | null, series_character_name: '', line_count: 3,
  series_pronouns: '', sample_lines: ['你好，师兄', '我们走吧'], ...over,
})

type Entry = ReturnType<typeof entry>
type Person = { id: number; character_name: string; aliases: string; notes: string; pronouns: string }

async function mockAll(page: Page) {
  const unmocked: string[] = []
  const posts: { path: string; body: Record<string, unknown> }[] = []
  let entries: Entry[] = [
    entry(),
    entry({
      speaker_label: 'SPEAKER_01', character_name: 'Lan Zhan', series_character_id: 12,
      series_character_name: 'Lan Zhan', series_pronouns: 'female', sample_lines: ['嗯'],
    }),
    entry({ speaker_label: 'SPEAKER_02', character_name: 'Jiang Cheng', pronouns: 'xe/xem', sample_lines: [] }),
  ]
  const cast: Person[] = [
    { id: 11, character_name: 'Wei Ying', aliases: '', notes: '', pronouns: 'he/him' },
    { id: 12, character_name: 'Lan Zhan', aliases: '', notes: '', pronouns: 'female' },
  ]
  let suggestions = [
    { speaker_label: 'SPEAKER_00', series_character_id: 11, character_name: 'Wei Ying', similarity: 0.91 },
    { speaker_label: 'SPEAKER_03', series_character_id: 12, character_name: 'Lan Zhan', similarity: 0.83 },
  ]
  const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, json: body })
  const update = (label: string, patch: object) => {
    entries = entries.map((e) => (e.speaker_label === label ? { ...e, ...patch } : e))
    return entries.find((e) => e.speaker_label === label)!
  }
  const record = (route: Route) => {
    const r = route.request()
    const body = JSON.parse(r.postData() ?? '{}') as Record<string, unknown>
    posts.push({ path: new URL(r.url()).pathname, body })
    return body
  }

  // Registered first, so it only catches what nothing below handles.
  await page.route('**/api/**', (route) => {
    const r = route.request()
    if (r.method() === 'GET') return route.continue()
    unmocked.push(`${r.method()} ${r.url()}`)
    return route.abort()
  })
  await page.route(/\/api\/auth\/me$/, (route) => json(route, ME.authOff))
  await page.route('**/api/library/dramas/1', async (route) => {
    const resp = await route.fetch()
    await route.fulfill({ response: resp, json: { ...(await resp.json()), series_id: 7 } })
  })
  await page.route('**/api/characters/dramas/1', (route) => json(route, entries))
  await page.route('**/api/characters/dramas/1/clone-engines', (route) =>
    json(route, { source_language: 'zh', default_engine: 'omnivoice', engines: [] }))
  await page.route('**/api/characters/voice-bank', (route) => json(route, []))
  await page.route('**/api/characters/series/7/characters', (route) => json(route, cast))
  await page.route('**/api/characters/dramas/1/voice-suggestions', (route) => json(route, suggestions))
  await page.route('**/api/characters/dramas/1/voice-suggestions/accept', (route) => {
    const b = record(route)
    const s = suggestions.find((x) => x.speaker_label === b.speaker_label && x.series_character_id === b.series_character_id)!
    suggestions = suggestions.filter((x) => x !== s)
    const character = update(s.speaker_label, {
      character_name: s.character_name, series_character_id: s.series_character_id,
      series_character_name: s.character_name, series_pronouns: 'he/him',
    })
    return json(route, { character, suggestions })
  })
  await page.route('**/api/characters/dramas/1/voice-suggestions/reject', (route) => {
    const b = record(route)
    suggestions = suggestions.filter((x) => !(x.speaker_label === b.speaker_label && x.series_character_id === b.series_character_id))
    return json(route, { character: null, suggestions })
  })
  await page.route('**/api/characters/dramas/1/character', (route) => {
    const b = record(route)
    const { speaker_label, ...patch } = b
    return json(route, update(String(speaker_label), patch))
  })
  await page.route('**/api/characters/dramas/1/remember-series-character', (route) => {
    const b = record(route)
    const e = entries.find((x) => x.speaker_label === b.speaker_label)!
    const person = { id: 13, character_name: e.character_name, aliases: '', notes: '', pronouns: e.pronouns }
    cast.push(person)
    const character = update(e.speaker_label, { series_character_id: 13, series_character_name: e.character_name })
    return json(route, { character, series_character: person, created: true })
  })
  return { unmocked, posts }
}

test('voice suggestions accept and reject', async ({ page }) => {
  const m = await mockAll(page)
  await page.goto('/#/drama/1/translate')
  await openSection(page, 'Characters')
  const sugg = page.getByRole('region', { name: 'Voice suggestions' })
  await expect(sugg.getByRole('listitem')).toHaveCount(2)
  await expect(sugg).toContainText('SPEAKER_00 sounds like Wei Ying')
  await expect(sugg).toContainText('similarity 0.91')
  await shot(page, 'characters-extras-suggestions-desktop')

  await sugg.getByRole('button', { name: 'Reject Lan Zhan for SPEAKER_03' }).click()
  await expect(sugg.getByRole('listitem')).toHaveCount(1)
  expect(m.posts.at(-1)).toEqual({
    path: '/api/characters/dramas/1/voice-suggestions/reject',
    body: { speaker_label: 'SPEAKER_03', series_character_id: 12 },
  })

  await sugg.getByRole('button', { name: 'Accept Wei Ying for SPEAKER_00' }).click()
  await expect(sugg).toBeHidden()
  // The row picks up the accepted name without a stale-form "unsaved change".
  await expect(page.getByLabel('Name for SPEAKER_00')).toHaveValue('Wei Ying')
  await expect(page.getByTestId('character-extras-SPEAKER_00')).toContainText('Shared with other dramas in this series')
  await expect(page.getByLabel('Gender for SPEAKER_00').locator('option').first()).toHaveText('Series default (he/him)')
  expect(m.posts.map((p) => p.path)).not.toContain('/api/characters/dramas/1/character')
  expect(m.unmocked).toEqual([])
})

test('samples, series default, custom pronouns and remember in this series', async ({ page }) => {
  const m = await mockAll(page)
  await page.goto('/#/drama/1/translate')
  await openSection(page, 'Characters')

  await expect(page.getByTestId('character-extras-SPEAKER_00')).toContainText('“你好，师兄” / “我们走吧”')
  await expect(page.getByTestId('character-extras-SPEAKER_02')).toContainText('No lines attributed to this speaker yet.')
  // Linked speaker: shared caption, series default named, no remember button.
  const lan = page.getByTestId('character-extras-SPEAKER_01')
  await expect(lan).toContainText('Shared with other dramas in this series')
  await expect(lan.getByRole('button')).toHaveCount(0)
  await expect(page.getByLabel('Gender for SPEAKER_01')).toHaveValue('')
  await expect(page.getByLabel('Gender for SPEAKER_01').locator('option:checked')).toHaveText('Series default (she/her)')

  // A stored custom value opens as Custom… with its text.
  await expect(page.getByLabel('Custom pronouns for SPEAKER_02')).toHaveValue('xe/xem')
  await page.getByLabel('Gender for SPEAKER_00').selectOption({ label: 'Custom…' })
  await page.getByLabel('Custom pronouns for SPEAKER_00').fill(' ze/zir ')
  await page.getByLabel('Name for SPEAKER_00').fill('Wen Ning')
  // Remember is offered only for a saved name.
  await expect(page.getByRole('button', { name: /^Remember/ })).toHaveCount(1) // SPEAKER_02
  await page.getByRole('row').filter({ has: page.getByLabel('Name for SPEAKER_00') }).getByRole('button', { name: 'Save' }).click()
  expect(m.posts.at(-1)).toEqual({
    path: '/api/characters/dramas/1/character',
    body: { speaker_label: 'SPEAKER_00', character_name: 'Wen Ning', pronouns: 'ze/zir' },
  })
  const remember = page.getByRole('button', { name: 'Remember Wen Ning in this series' })
  await expect(remember).toBeEnabled()

  await openSection(page, 'Series cast')
  await expect(page.getByTestId('series-cast').locator('li')).toHaveCount(2)
  await remember.click()
  await expect(page.getByRole('status').filter({ hasText: 'Added Wen Ning to the series cast.' })).toBeVisible()
  expect(m.posts.at(-1)).toEqual({
    path: '/api/characters/dramas/1/remember-series-character',
    body: { speaker_label: 'SPEAKER_00' },
  })
  await expect(page.getByTestId('series-cast').locator('li')).toHaveCount(3)
  await expect(remember).toBeHidden()
  await expect(page.getByTestId('character-extras-SPEAKER_00')).toContainText('Shared with other dramas in this series')
  await shot(page, 'characters-extras-desktop')
  expect(m.unmocked).toEqual([])
})

test('fits a 390px phone width', async ({ page }) => {
  const m = await mockAll(page)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/#/drama/1/translate')
  await openSection(page, 'Characters')
  await expect(page.getByRole('region', { name: 'Voice suggestions' }).getByRole('listitem')).toHaveCount(2)
  await expect(page.getByTestId('character-extras-SPEAKER_00')).toBeVisible()
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
  const accept = page.getByRole('button', { name: 'Accept Wei Ying for SPEAKER_00' })
  const box = await accept.boundingBox()
  expect((box?.x ?? 0) + (box?.width ?? 0)).toBeLessThanOrEqual(390)
  await shot(page, 'characters-extras-phone')
  expect(m.unmocked).toEqual([])
})

test('a refused remember (409, already linked) shows in the row error banner', async ({ page }) => {
  const m = await mockAll(page)
  // Registered after mockAll, so it wins: the speaker was linked elsewhere
  // (another tab, an accepted suggestion) since this page loaded.
  await page.route('**/api/characters/dramas/1/remember-series-character', (route) =>
    route.fulfill({
      status: 409,
      json: { error: { code: 'conflict', message: 'This speaker is already linked to a character in this series.' } },
    }))
  await page.goto('/#/drama/1/translate')
  await openSection(page, 'Characters')
  await page.getByRole('button', { name: 'Remember Jiang Cheng in this series' }).click()
  const row = page.getByRole('row').filter({ has: page.getByLabel('Name for SPEAKER_02') })
  const banner = row.getByRole('alert')
  await expect(banner).toContainText('That cannot be done right now')
  await expect(banner).toContainText('This speaker is already linked to a character in this series.')
  await expect(page.getByRole('status').filter({ hasText: 'Added Jiang Cheng' })).toHaveCount(0)
  await banner.getByRole('button', { name: 'Dismiss' }).click()
  await expect(row.getByRole('alert')).toHaveCount(0)
  expect(m.unmocked).toEqual([])
})
