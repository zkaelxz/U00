import { expect, test, type Page, type Route } from '@playwright/test'

import { ME } from './authMocks'

// Translate > Characters: merge one speaker into another, with a confirm step
// that shows how many lines move, and one Undo.
// Every characters route is mocked and any unmocked write is aborted, so the
// shared library is never written.

test.use({ viewport: { width: 1440, height: 900 } })

const SHOTS = process.env.SHOT_DIR

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

const entry = (label: string, over: object = {}) => ({
  speaker_label: label, character_name: '', voice_actor: '', pronouns: '', tts_voice: '',
  offline_voice: '', clone_engine: '', voice_design: '', has_ref_audio: false, ref_text_present: false,
  series_character_id: null, series_character_name: '', line_count: 3, series_pronouns: '',
  sample_lines: [], ...over,
})

async function mockAll(page: Page) {
  const unmocked: string[] = []
  const posts: { path: string; body: Record<string, unknown> }[] = []
  const start = () => [entry('SPEAKER_01', { line_count: 4 }), entry('SPEAKER_03', { line_count: 3 })]
  let entries = start()
  const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, json: body })
  const undo = { undo_id: 'u'.repeat(32), expires_in: 900 }

  await page.route('**/api/**', (route) => {
    const r = route.request()
    if (r.method() === 'GET') return route.continue()
    unmocked.push(`${r.method()} ${r.url()}`)
    return route.abort()
  })
  await page.route(/\/api\/auth\/me$/, (route) => json(route, ME.authOff))
  await page.route('**/api/characters/dramas/1', (route) => json(route, entries))
  await page.route('**/api/characters/dramas/1/clone-engines', (route) =>
    json(route, { source_language: 'zh', default_engine: 'f5tts', engines: [] }))
  await page.route('**/api/characters/voice-bank', (route) => json(route, []))
  await page.route('**/api/characters/dramas/1/merge-speakers', (route) => {
    posts.push({ path: '/merge-speakers', body: JSON.parse(route.request().postData() ?? '{}') as Record<string, unknown> })
    entries = [entry('SPEAKER_01', { line_count: 7 })]
    return json(route, { characters: entries, moved: 3, undo })
  })
  await page.route('**/api/characters/dramas/1/merge-speakers/undo', (route) => {
    posts.push({ path: '/merge-speakers/undo', body: JSON.parse(route.request().postData() ?? '{}') as Record<string, unknown> })
    entries = start()
    return json(route, { characters: entries, moved: 3, undo: null })
  })
  return { unmocked, posts, undo }
}

async function openCharacters(page: Page) {
  await page.goto('/#/drama/1/translate')
  const summary = page.locator('summary').filter({ has: page.locator('.section-title', { hasText: /^Characters$/ }) }).first()
  if ((await summary.locator('xpath=..').getAttribute('open')) === null) await summary.click()
}

async function startMerge(page: Page) {
  await page.getByRole('button', { name: 'Merge into…' }).nth(1).click()
  await page.getByLabel('Merge SPEAKER_03 into').selectOption('SPEAKER_01')
}

test('merge with a confirm step, then undo', async ({ page }) => {
  const m = await mockAll(page)
  await openCharacters(page)

  await page.getByRole('button', { name: 'Merge into…' }).nth(1).click()
  const confirm = page.getByRole('button', { name: /^Merge 3 lines/ })
  await expect(page.getByRole('button', { name: 'Merge', exact: true })).toBeDisabled() // nothing chosen yet
  await page.getByLabel('Merge SPEAKER_03 into').selectOption('SPEAKER_01')
  await expect(page.locator('.character-merge-confirm')).toContainText('3 lines from SPEAKER_03 will move to SPEAKER_01')
  expect(m.posts).toEqual([]) // nothing is sent before the confirm
  await confirm.click()

  expect(m.posts).toEqual([{ path: '/merge-speakers', body: { source_label: 'SPEAKER_03', target_label: 'SPEAKER_01' } }])
  await expect(page.getByLabel('Name for SPEAKER_03')).toHaveCount(0)
  await expect(page.locator('.character-notice')).toContainText('Merged SPEAKER_03 into SPEAKER_01: 3 lines moved.')
  expect((await page.getByRole('button', { name: 'Undo merge' }).boundingBox())!.height).toBeGreaterThanOrEqual(32)
  await page.getByRole('button', { name: 'Undo merge' }).click()
  expect(m.posts.at(-1)).toEqual({ path: '/merge-speakers/undo', body: { undo_id: m.undo.undo_id } })
  await expect(page.getByLabel('Name for SPEAKER_03')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Undo merge' })).toHaveCount(0)
  expect(m.unmocked).toEqual([])
})

test('editing the target clears the undo', async ({ page }) => {
  const m = await mockAll(page)
  await openCharacters(page)
  await startMerge(page)
  await page.getByRole('button', { name: /^Merge 3 lines/ }).click()
  await expect(page.getByRole('button', { name: 'Undo merge' })).toBeVisible()
  await page.route('**/api/characters/dramas/1/character', (route) =>
    route.fulfill({ status: 200, json: entry('SPEAKER_01', { line_count: 7, character_name: 'Mei' }) }))
  await page.getByLabel('Name for SPEAKER_01').fill('Mei')
  await page.locator('button:enabled', { hasText: /^Save$/ }).first().click()
  await expect(page.getByRole('button', { name: 'Undo merge' })).toHaveCount(0)
  expect(m.posts.map((p) => p.path)).toEqual(['/merge-speakers'])
})

test('phone: no sideways scroll and 44px targets', async ({ page }) => {
  const m = await mockAll(page)
  await page.setViewportSize({ width: 390, height: 844 })
  await openCharacters(page)
  await startMerge(page)
  for (const loc of [page.getByLabel('Merge SPEAKER_03 into'), page.getByRole('button', { name: /^Merge 3 lines/ }),
    page.getByRole('button', { name: 'Cancel' })]) {
    const box = await loc.boundingBox()
    expect(box!.height).toBeGreaterThanOrEqual(44)
  }
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth,
  }))
  expect(scroll).toBeLessThanOrEqual(client)
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/characters-merge-phone.png`, fullPage: true })
  expect(m.unmocked).toEqual([])
})

test('desktop: controls are at least 32px tall', async ({ page }) => {
  await mockAll(page)
  await openCharacters(page)
  await startMerge(page)
  for (const loc of [page.getByLabel('Merge SPEAKER_03 into'), page.getByRole('button', { name: /^Merge 3 lines/ })]) {
    expect((await loc.boundingBox())!.height).toBeGreaterThanOrEqual(32)
  }
})
