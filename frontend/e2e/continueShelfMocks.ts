// The Library's Continue shelf and the title list it reads its labels from, mocked so its tiles stand in known states.
import type { Page } from '@playwright/test'

const shelfRow = (id: number, title_en: string, media_type: string, over: Record<string, unknown> = {}) => ({
  id, title_en, title_zh: null, author: null, studio: null, director: null, voice_actors: null, status: 'aligned',
  source_language: 'zh', media_type, content_mode: null, series_id: null, translation_engine: null, custom_tags: [],
  created_at: null, updated_at: '2026-11-10T12:00:00', ...over,
})
type ShelfRow = ReturnType<typeof shelfRow>

// Newest first: Blessing read to page 12; Grandmaster with lines left; Signal translated (the check state); Harbor
// Lights exported; Quiet Stream also in the check state, past the four a computer shows until More.
const shelfRows = (): ShelfRow[] => [
  shelfRow(1, 'Grandmaster of Demonic Cultivation', 'audio_drama', { line_count: 2000, translated_count: 766 }),
  shelfRow(2, "Heaven Official's Blessing", 'novel', { line_count: 90, translated_count: 90 }),
  shelfRow(3, 'Signal', 'video_drama', { line_count: 40, translated_count: 40, status: 'translated' }),
  shelfRow(61, 'Harbor Lights', 'video_drama', { line_count: 12, translated_count: 12, status: 'exported' }),
  shelfRow(62, 'Quiet Stream', 'asmr', { line_count: 8, translated_count: 8, status: 'translated' }),
]

export function gate() {
  let open!: () => void
  const shut = new Promise<void>((r) => { open = r })
  return { shut, open }
}

const fail = { status: 500, json: { error: { code: 'internal', message: 'Something went wrong.' } } }

/**
 * Routes the shelf's requests to a controller a test can change between requests: `rows` (title 2 is the reading
 * entry, the rest are recent work), `risk` (a count per title, null answers 500), `holdList` / `holdCount` (each
 * answer waits on it), `failList`. The list filters by search and status like the real one. `asked` and
 * `answered` record the count requests by title, in order.
 */
export async function mockShelf(page: Page, risk: (id: number) => number | null) {
  const shelf = {
    rows: shelfRows(),
    risk,
    holdList: null as Promise<void> | null,
    holdCount: null as Promise<void> | null,
    failList: false,
    asked: [] as number[],
    answered: [] as number[],
  }
  await page.route('**/api/library/continue', (r) => r.fulfill({ json: { items: [{
    drama_id: 2, title_en: "Heaven Official's Blessing", title_zh: null, percent_complete: 40, last_page: 12,
    last_accessed_at: '2026-11-10T12:00:09', has_cover_art: false,
  }] } }))
  await page.route('**/api/library/recent', (r) => r.fulfill({ json: { items: shelf.rows.filter((d) => d.id !== 2).map((d, n) => ({
    id: d.id, title_en: d.title_en, title_zh: null, status: d.status, media_type: d.media_type, updated_at: `2026-11-10T12:00:0${8 - n}`,
  })) } }))
  await page.route((u) => u.pathname === '/api/library/dramas', async (r) => {
    if (r.request().method() !== 'GET') return r.fallback()
    await shelf.holdList
    if (shelf.failList) return r.fulfill(fail)
    const q = new URL(r.request().url()).searchParams
    const search = (q.get('search') ?? '').toLowerCase()
    const status = q.get('status') ?? ''
    const items = shelf.rows.filter((d) => d.title_en.toLowerCase().includes(search) && (!status || d.status === status))
    await r.fulfill({ json: { items, count: items.length } })
  })
  await page.route('**/api/review/dramas/*/lines?*', async (r) => {
    const url = new URL(r.request().url())
    if (url.searchParams.get('only') !== 'risk') return r.fallback()
    const id = Number(url.pathname.split('/')[4])
    shelf.asked.push(id)
    await shelf.holdCount
    const n = shelf.risk(id)
    await (n === null ? r.fulfill(fail) : r.fulfill({ json: { items: [], total: n, page: 1, page_size: 1, risk_count: n } }))
    shelf.answered.push(id)
  })
  return shelf
}

/** Lets the page take in an answer the route already sent: two frames, so React has rendered whatever it changed. */
export const settle = (page: Page) =>
  page.evaluate(() => new Promise<void>((done) => requestAnimationFrame(() => requestAnimationFrame(() => done()))))

/** The page reloads its data by itself every 5 s while a load failed and Baihe answers; a failing health probe keeps
 *  a test's Retry (or its other reload) the only one. */
export const keepBaiheUnreachable = (page: Page) =>
  page.route('**/api/health', (r) => r.fulfill(fail))

/** A viewer whose shelf last had `count` things to continue: the placeholder shows that many while it loads. */
export const rememberShelfCount = (page: Page, count: number) =>
  page.addInitScript((n) => localStorage.setItem('baihe.pref.library.continue.count', String(n)), count)

/** `count` reading entries with one-line titles and no work, and the title list held until the returned call. */
export async function holdShelfOf(page: Page, count: number) {
  await rememberShelfCount(page, count)
  await page.route('**/api/library/continue', (r) => r.fulfill({ json: { items: Array.from({ length: count }, (_, i) => ({
    drama_id: 100 + i, title_en: `Book ${i + 1}`, title_zh: null, percent_complete: 40, last_page: 12,
    last_accessed_at: `2026-11-10T12:00:${String(10 + i)}`, has_cover_art: false,
  })) } }))
  await page.route('**/api/library/recent', (r) => r.fulfill({ json: { items: [] } }))
  const list = gate()
  await page.route((u) => u.pathname === '/api/library/dramas', async (r) => {
    await list.shut
    await r.fallback()
  })
  return list.open
}

/** The placeholder's height, then the loaded shelf's once the list is let through, after the fonts have loaded. */
export async function placeholderAndShelf(page: Page, release: () => void) {
  await page.evaluate(() => document.fonts.ready.then(() => undefined))
  const placeholder = page.locator('.continue-placeholder')
  await placeholder.waitFor()
  const before = (await placeholder.boundingBox())!.height
  release()
  const shelf = page.getByRole('region', { name: 'Continue' })
  await shelf.waitFor()
  await page.evaluate(() => document.fonts.ready.then(() => undefined))
  return { placeholder: before, shelf: (await shelf.boundingBox())!.height }
}
