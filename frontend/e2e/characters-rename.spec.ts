import { expect, test, type Page, type Route } from '@playwright/test'

import { ME } from './authMocks'

// Translate > Characters: name a speaker once. One call renames every line and
// the Characters row; Undo (kept for the session) puts the old label back.
// Every characters route is mocked and any unmocked write is aborted, so the
// shared library is never written.

test.use({ viewport: { width: 1440, height: 900 } })

const SHOTS = process.env.SHOT_DIR

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

const entry = (label: string, over: object = {}) => ({
  speaker_label: label, character_name: '', voice_actor: '', pronouns: '',
  clone_engine: '', voice_design: '', has_ref_audio: false, ref_text_present: false,
  series_character_id: null, series_character_name: '', line_count: 3, series_pronouns: '',
  sample_lines: [], ...over,
})

async function mockAll(page: Page) {
  const unmocked: string[] = []
  const posts: { path: string; body: Record<string, unknown> }[] = []
  let entries = [entry('Speaker 1'), entry('Speaker 2', { line_count: 2 })]
  const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, json: body })
  const undo = {
    speaker_label: 'Mei', previous_label: 'Speaker 1', previous_character_name: null,
    previous: [{ id: 1, speaker: 'Speaker 1', speaker_manual: false }],
  }

  await page.route('**/api/**', (route) => {
    const r = route.request()
    if (r.method() === 'GET') return route.continue()
    unmocked.push(`${r.method()} ${r.url()}`)
    return route.abort()
  })
  await page.route(/\/api\/auth\/me$/, (route) => json(route, ME.authOff))
  await page.route('**/api/characters/dramas/1', (route) => json(route, entries))
  await page.route('**/api/characters/dramas/1/clone-engines', (route) =>
    json(route, { source_language: 'zh', default_engine: 'omnivoice', engines: [] }))
  await page.route('**/api/characters/voice-bank', (route) => json(route, []))
  await page.route('**/api/characters/dramas/1/rename-speaker', (route) => {
    const body = JSON.parse(route.request().postData() ?? '{}') as Record<string, unknown>
    posts.push({ path: '/rename-speaker', body })
    entries = entries.map((e) => (e.speaker_label === body.speaker_label
      ? { ...e, speaker_label: String(body.new_name), character_name: String(body.new_name) } : e))
    return json(route, { characters: entries, renamed: 3, undo })
  })
  await page.route('**/api/characters/dramas/1/rename-speaker/undo', (route) => {
    posts.push({ path: '/rename-speaker/undo', body: JSON.parse(route.request().postData() ?? '{}') })
    entries = [entry('Speaker 1'), entry('Speaker 2', { line_count: 2 })]
    return json(route, { characters: entries, renamed: 3, undo: null })
  })
  return { unmocked, posts, undo }
}

async function openCharacters(page: Page) {
  await page.goto('/#/drama/1/translate')
  const summary = page.locator('summary').filter({ has: page.locator('.section-title', { hasText: /^Characters$/ }) }).first()
  if ((await summary.locator('xpath=..').getAttribute('open')) === null) await summary.click()
}

test('rename once, then undo, and the undo survives a reload', async ({ page }) => {
  const m = await mockAll(page)
  await openCharacters(page)

  const row = page.getByRole('row').filter({ has: page.getByLabel('Name for Speaker 1') })
  const extras = row.locator('xpath=following-sibling::tr[1]')
  await extras.getByRole('button', { name: 'Rename speaker' }).click()
  const submit = extras.getByRole('button', { name: /^Rename on all 3 lines/ })
  await expect(submit).toBeDisabled() // blank
  await extras.getByLabel('New name for Speaker 1').fill('Speaker 2')
  await expect(submit).toBeDisabled() // another speaker has it
  await extras.getByLabel('New name for Speaker 1').fill(' Mei ')
  await submit.click()

  expect(m.posts).toEqual([{ path: '/rename-speaker', body: { speaker_label: 'Speaker 1', new_name: 'Mei' } }])
  await expect(page.getByLabel('Name for Mei')).toHaveValue('Mei')
  await expect(page.getByLabel('Name for Speaker 1')).toHaveCount(0)
  await expect(page.locator('.character-notice')).toContainText('Renamed Speaker 1 to Mei on 3 lines.')
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${SHOTS}/characters-rename-desktop.png`, fullPage: true })

  await page.reload()
  await page.getByRole('button', { name: 'Undo rename' }).waitFor()
  await page.getByRole('button', { name: 'Undo rename' }).click()
  expect(m.posts.at(-1)).toEqual({ path: '/rename-speaker/undo', body: { undo: m.undo } })
  await expect(page.getByLabel('Name for Speaker 1')).toBeVisible()
  await expect(page.locator('.character-notice')).toContainText('Put Speaker 1 back on 3 lines.')
  await expect(page.getByRole('button', { name: 'Undo rename' })).toHaveCount(0)
  expect(m.unmocked).toEqual([])
})

test('undo survives a busy 409 or a lost connection, and goes on a refusal', async ({ page }) => {
  const m = await mockAll(page)
  const answers = [
    { status: 409, json: { error: { code: 'conflict', message: 'A background job is still running.', details: { reason: 'job_running' } } } },
    null,
    { status: 409, json: { error: { code: 'conflict', message: 'The lines changed since the rename, so it can\'t be undone.' } } },
  ]
  await page.route('**/api/characters/dramas/1/rename-speaker/undo', (route) => {
    const next = answers.shift()
    return next ? route.fulfill(next) : route.abort()
  })
  await openCharacters(page)
  const row = page.getByRole('row').filter({ has: page.getByLabel('Name for Speaker 1') })
  await row.locator('xpath=following-sibling::tr[1]').getByRole('button', { name: 'Rename speaker' }).click()
  await page.getByLabel('New name for Speaker 1').fill('Mei')
  await page.getByRole('button', { name: /^Rename on all 3 lines/ }).click()

  const undo = page.getByRole('button', { name: 'Undo rename' })
  await undo.click()
  await expect(page.getByText('A background job is still running.')).toBeVisible()
  await expect(undo).toBeEnabled()
  await undo.click()
  await expect(undo).toBeEnabled()
  await undo.click()
  await expect(page.getByText(/The lines changed since the rename/)).toBeVisible()
  await expect(undo).toHaveCount(0)
  expect(m.unmocked).toEqual([])
})

test('a rename elsewhere keeps what is typed in another row', async ({ page }) => {
  const m = await mockAll(page)
  await openCharacters(page)
  await page.getByLabel('Name for Speaker 2').fill('Typed, not saved')
  const row = page.getByRole('row').filter({ has: page.getByLabel('Name for Speaker 1') })
  await row.locator('xpath=following-sibling::tr[1]').getByRole('button', { name: 'Rename speaker' }).click()
  await page.getByLabel('New name for Speaker 1').fill('Mei')
  await page.getByRole('button', { name: /^Rename on all 3 lines/ }).click()
  await expect(page.getByLabel('Name for Mei')).toHaveValue('Mei')
  await expect(page.getByLabel('Name for Speaker 2')).toHaveValue('Typed, not saved')
  expect(m.unmocked).toEqual([])
})

test('phone: no sideways scroll and 44px targets', async ({ page }) => {
  const m = await mockAll(page)
  await page.setViewportSize({ width: 390, height: 844 })
  await openCharacters(page)
  await page.getByRole('button', { name: 'Rename speaker' }).first().click()
  await page.getByLabel('New name for Speaker 1').fill('Mei')
  for (const loc of [page.getByLabel('New name for Speaker 1'), page.getByRole('button', { name: /^Rename on all/ }),
    page.getByRole('button', { name: 'Cancel' })]) {
    const box = await loc.boundingBox()
    expect(box!.height).toBeGreaterThanOrEqual(44)
  }
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth,
  }))
  expect(scroll).toBeLessThanOrEqual(client)
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/characters-rename-phone.png`, fullPage: true })
  expect(m.unmocked).toEqual([])
})
