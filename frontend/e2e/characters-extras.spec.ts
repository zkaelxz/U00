import { expect, test, type Page, type Route } from '@playwright/test'
import { shot } from './shots'

import { expectNoSidewaysScroll, json, mockAppShell } from './mockHelpers'
import { showTranslateCard } from './translateTabs'

// Translate > Characters extras (inventory C02, C04, C07, C08): voice-match
// suggestions (accept/reject), sample lines and the "shared with series"
// caption, custom pronouns with the series default, "remember in this
// series", and the sample clips to hear each speaker. The drama read hits the real seeded API (reported as series 7);
// every characters route is mocked and any unmocked write is aborted and
// recorded, so the shared library is never written.

test.use({ viewport: { width: 1280, height: 800 } })


test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

async function openSection(page: Page, title: string) {
  await showTranslateCard(page, title)
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
type Clip = { id: string; start: number; end: number; duration: number; ref_text: string }
type SpeakerClips = { speaker_label: string; candidates: Clip[]; skip_reason: string | null; closest_duration: number | null }

const clip = (n: number, ref_text = ''): Clip => ({ id: String(n).repeat(32).slice(0, 32), start: n * 10, end: n * 10 + 4.5, duration: 4.5, ref_text })

// Silence, so the browser really plays (and, if short, ends) it.
function silentWav(seconds: number): Buffer {
  const rate = 8000
  const data = Math.round(rate * seconds)
  const b = Buffer.alloc(44 + data)
  b.write('RIFF', 0); b.writeUInt32LE(36 + data, 4); b.write('WAVE', 8)
  b.write('fmt ', 12); b.writeUInt32LE(16, 16); b.writeUInt16LE(1, 20); b.writeUInt16LE(1, 22)
  b.writeUInt32LE(rate, 24); b.writeUInt32LE(rate, 28); b.writeUInt16LE(1, 32); b.writeUInt16LE(8, 34)
  b.write('data', 36); b.writeUInt32LE(data, 40); b.fill(128, 44)
  return b
}
type Person = { id: number; character_name: string; aliases: string; notes: string; pronouns: string }

async function mockAll(page: Page) {
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

  // SPEAKER_01 already has clips from an earlier search; the others have none.
  let clips: SpeakerClips[] = [
    { speaker_label: 'SPEAKER_01', candidates: [clip(1, '嗯'), clip(2)], skip_reason: null, closest_duration: null },
  ]
  // What the next search finds, per speaker; anyone not listed gets a skip.
  const found: Record<string, Clip[]> = { SPEAKER_00: [clip(3, '你好，师兄'), clip(4, '我们走吧'), clip(5)] }
  const job = { searchFor: null as string | null, polls: 0 }
  const audioFetched: string[] = []

  const unmocked = await mockAppShell(page)
  await page.route('**/api/library/dramas/1', async (route) => {
    const resp = await route.fetch()
    await route.fulfill({ response: resp, json: { ...(await resp.json()), series_id: 7, has_audio: true } })
  })
  await page.route('**/api/characters/dramas/1/reference-clips/candidates', (route) =>
    json(route, { drama_id: 1, speakers: clips }))
  await page.route('**/api/characters/dramas/1/reference-clips/candidates/*/audio', (route) => {
    const id = new URL(route.request().url()).pathname.split('/').at(-2)!
    audioFetched.push(id)
    // Clip 1 is long, so only the page's one-at-a-time stop can end it within the test.
    return route.fulfill({ status: 200, contentType: 'audio/wav', body: silentWav(id === clip(1).id ? 30 : 0.5) })
  })
  // The server files a clip under whoever the line it was cut from belongs to, so a swap trades them.
  await page.route('**/api/characters/dramas/1/swap-speakers', (route) => {
    const b = record(route)
    const other = (l: string) => (l === b.first ? String(b.second) : l === b.second ? String(b.first) : l)
    clips = clips.map((c) => ({ ...c, speaker_label: other(c.speaker_label) }))
    return json(route, { updated: 4, line_ids: [1, 2, 3, 4], skipped_ids: [], undo: [] })
  })
  await page.route('**/api/characters/dramas/1/reference-clips/extract', (route) => {
    const b = record(route)
    job.searchFor = String(b.speaker_label)
    job.polls = 0
    return json(route, { job_id: 'voiceref_1' })
  })
  await page.route('**/api/jobs/voiceref_1', (route) => {
    if (job.searchFor === null) return route.fulfill({ status: 404, json: { error: { code: 'not_found', message: 'No such job.' } } })
    job.polls += 1
    const done = job.polls > 1
    if (done) {
      const label = job.searchFor
      const got = found[label] ?? []
      clips = [...clips.filter((c) => c.speaker_label !== label), {
        speaker_label: label, candidates: got,
        skip_reason: got.length ? null : 'too_short', closest_duration: got.length ? null : 1.4,
      }]
    }
    return json(route, {
      job_id: 'voiceref_1', status: done ? 'done' : 'running', progress: done ? 1 : 0.5, message: '',
      error: null, description: 'Reference clip extraction (title #1)', gpu_touching: false,
      started_at: 1, finished_at: null, updated_at: 1, outcome: done ? 'ok' : null,
    })
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
  return { unmocked, posts, audioFetched }
}

test('voice suggestions accept and reject', async ({ page }) => {
  const m = await mockAll(page)
  await page.goto('/#/drama/1/translate')
  await openSection(page, 'Characters')
  const sugg = page.getByRole('region', { name: 'Voice suggestions' })
  await expect(sugg.getByRole('listitem')).toHaveCount(2)
  await expect(sugg).toContainText('SPEAKER_00 sounds like Wei Ying')
  await expect(sugg).toContainText('similarity 0.91')
  await shot(page, 'characters-extras-suggestions-desktop', true)

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
  await expect(page.getByTestId('character-extras-SPEAKER_00')).toContainText('Shared with other titles in this series')
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
  await expect(lan).toContainText('Shared with other titles in this series')
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
  await expect(page.getByTestId('character-extras-SPEAKER_00')).toContainText('Shared with other titles in this series')
  await shot(page, 'characters-extras-desktop', true)
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
  await shot(page, 'characters-extras-phone', true)
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

test('characters without permission to read lines: a plain note, no error banner', async ({ page }) => {
  await mockAll(page)
  await page.route('**/api/characters/dramas/1', (route) =>
    route.fulfill({ status: 403, json: { error: { code: 'forbidden', message: 'Not allowed.' } } }))
  await page.goto('/#/drama/1/translate')
  await openSection(page, 'Characters')
  const region = page.getByRole('region', { name: 'Characters' })
  await expect(region.getByText('Seeing line text needs permission to read lines. Ask the PC owner.')).toBeVisible()
  await expect(region.getByRole('alert')).toHaveCount(0)
})

test('sample clips: play one, find clips for a speaker, and a speaker with no clean clip', async ({ page }) => {
  const m = await mockAll(page)
  await page.goto('/#/drama/1/translate')
  await openSection(page, 'Characters')
  const region = page.getByRole('region', { name: 'Characters' })
  await expect(region.getByRole('columnheader', { name: 'Sample' })).toBeVisible()

  // Clips from an earlier search play straight away, one at a time.
  const lan = region.getByRole('group', { name: 'Sample clips for SPEAKER_01' })
  await expect(lan.getByRole('button')).toHaveText(['▶ 1', '▶ 2'])
  await expect(lan.getByRole('button', { name: 'Play sample 1 for SPEAKER_01' })).toHaveAttribute('title', '4.5 s · 嗯')
  await lan.getByRole('button', { name: 'Play sample 1 for SPEAKER_01' }).click()
  await expect(lan.getByRole('button', { name: 'Stop sample 1 for SPEAKER_01' })).toHaveAttribute('aria-pressed', 'true')
  await expect.poll(() => m.audioFetched).toEqual([clip(1).id])
  // Starting another clip stops the first (it is 30 s long, so nothing else would).
  await lan.getByRole('button', { name: 'Play sample 2 for SPEAKER_01' }).click()
  await expect(lan.getByRole('button', { name: 'Play sample 1 for SPEAKER_01' })).toHaveAttribute('aria-pressed', 'false')
  // The half-second clip ends by itself and the button goes back to Play.
  await expect(lan.getByRole('button', { name: 'Play sample 2 for SPEAKER_01' })).toHaveAttribute('aria-pressed', 'false')
  await expect(page.getByText("Couldn't play this clip")).toHaveCount(0)

  // A speaker with no clips yet: Find clips runs the search and shows them.
  await region.getByRole('button', { name: 'Find sample clips for SPEAKER_00' }).click()
  await expect.poll(() => m.posts.at(-1)).toEqual({
    path: '/api/characters/dramas/1/reference-clips/extract',
    body: { speaker_label: 'SPEAKER_00', max_candidates: 3 },
  })
  const wei = region.getByRole('group', { name: 'Sample clips for SPEAKER_00' })
  await expect(wei.getByRole('button')).toHaveText(['▶ 1', '▶ 2', '▶ 3'])
  await expect(region.getByRole('button', { name: 'Find sample clips for SPEAKER_02' })).toBeEnabled()
  await shot(page, 'characters-samples-desktop', true)

  // No line long enough: the reason shows under the row and Find clips stays.
  await region.getByRole('button', { name: 'Find sample clips for SPEAKER_02' }).click()
  await expect(page.getByTestId('sample-notes-SPEAKER_02')).toContainText('No clip found: the closest was 1.4 s, under the 3 s minimum')
  await expect(region.getByRole('button', { name: 'Find sample clips for SPEAKER_02' })).toBeEnabled()
  expect(m.unmocked).toEqual([])
})

test('sample clips: a refused search shows in that speaker\'s row', async ({ page }) => {
  const m = await mockAll(page)
  await page.route('**/api/characters/dramas/1/reference-clips/extract', (route) =>
    route.fulfill({ status: 409, json: { error: { code: 'conflict', message: 'A reference clip extraction is already running for this title.' } } }))
  await page.goto('/#/drama/1/translate')
  await openSection(page, 'Characters')
  await page.getByRole('button', { name: 'Find sample clips for SPEAKER_00' }).click()
  const notes = page.getByTestId('sample-notes-SPEAKER_00')
  await expect(notes.getByRole('alert')).toContainText('A reference clip extraction is already running for this title.')
  await expect(page.getByRole('button', { name: 'Find sample clips for SPEAKER_00' })).toHaveText('Find clips')
  await notes.getByRole('button', { name: 'Dismiss' }).click()
  await expect(notes).toHaveCount(0)
  expect(m.unmocked).toEqual([])
})

test('sample clips: no Sample column on a title without audio', async ({ page }) => {
  await mockAll(page)
  await page.route('**/api/library/dramas/1', async (route) => {
    const resp = await route.fetch()
    await route.fulfill({ response: resp, json: { ...(await resp.json()), series_id: 7, has_audio: false } })
  })
  await page.goto('/#/drama/1/translate')
  await openSection(page, 'Characters')
  const region = page.getByRole('region', { name: 'Characters' })
  await expect(page.getByLabel('Name for SPEAKER_00')).toBeVisible()
  await expect(region.getByRole('columnheader', { name: 'Sample' })).toHaveCount(0)
  await expect(region.getByRole('button', { name: /^Find sample clips/ })).toHaveCount(0)
})

test('sample clips fit a 390px phone width', async ({ page }) => {
  const m = await mockAll(page)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/#/drama/1/translate')
  await openSection(page, 'Characters')
  await page.getByRole('button', { name: 'Find sample clips for SPEAKER_00' }).click()
  const wei = page.getByRole('group', { name: 'Sample clips for SPEAKER_00' })
  await expect(wei.getByRole('button')).toHaveCount(3)
  await expectNoSidewaysScroll(page)
  for (const b of await page.getByRole('button', { name: /^(Play sample|Find sample clips)/ }).all()) {
    const box = await b.boundingBox()
    expect((box?.x ?? 0) + (box?.width ?? 0)).toBeLessThanOrEqual(390)
  }
  await wei.scrollIntoViewIfNeeded()
  await shot(page, 'characters-samples-phone')
  expect(m.unmocked).toEqual([])
})

test('sample clips: a swap moves the clips to the speaker who now has their lines', async ({ page }) => {
  const m = await mockAll(page)
  await page.goto('/#/drama/1/translate')
  await openSection(page, 'Characters')
  const region = page.getByRole('region', { name: 'Characters' })
  await expect(region.getByRole('group', { name: 'Sample clips for SPEAKER_01' }).getByRole('button')).toHaveCount(2)
  await expect(region.getByRole('button', { name: 'Find sample clips for SPEAKER_00' })).toBeVisible()
  // Rows are in speaker order, so the second Swap belongs to SPEAKER_01.
  await region.getByRole('button', { name: 'Swap speakers…' }).nth(1).click()
  await page.getByLabel('Swap SPEAKER_01 with').selectOption('SPEAKER_00')
  await page.getByRole('button', { name: 'Swap', exact: true }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Swapped SPEAKER_01 and SPEAKER_00' })).toBeVisible()
  await expect(region.getByRole('group', { name: 'Sample clips for SPEAKER_00' }).getByRole('button')).toHaveCount(2)
  await expect(region.getByRole('button', { name: 'Find sample clips for SPEAKER_01' })).toBeVisible()
  await expect(region.getByRole('button', { name: 'Play sample 1 for SPEAKER_00' })).toHaveAttribute('title', '4.5 s · 嗯')
  expect(m.unmocked).toEqual([])
})

test('sample clips: a search the page loses track of says so', async ({ page }) => {
  const m = await mockAll(page)
  // The job record is gone (say the server restarted) as soon as it starts.
  await page.route('**/api/characters/dramas/1/reference-clips/extract', (route) => json(route, { job_id: 'voiceref_1' }))
  await page.route('**/api/jobs/voiceref_1', (route) =>
    route.fulfill({ status: 404, json: { error: { code: 'not_found', message: 'No such job.' } } }))
  await page.goto('/#/drama/1/translate')
  await openSection(page, 'Characters')
  await page.getByRole('button', { name: 'Find sample clips for SPEAKER_00' }).click()
  await expect(page.getByTestId('sample-notes-SPEAKER_00')).toContainText("Couldn't follow the search for clips")
  await expect(page.getByRole('button', { name: 'Find sample clips for SPEAKER_00' })).toHaveText('Find clips')
  expect(m.unmocked).toEqual([])
})
