import { expect, type Page, type Route } from '@playwright/test'

import { ME, REMOTE_HEALTH_OFF } from './authMocks'
import { pagePng } from './comicMocks'

// Saved manga mocks (/api/saved-comics/*) plus the app shell's calls. A
// catch-all aborts (and records) every /api call nothing here mocks.

export const SOURCE = 'MangaK'
export const SERIES = 'Test Camp'
const CHAPTERS = ['0001 Chapter 1', '0002 Chapter 2']
export const W = 600
const H = 900

export interface State {
  calls: string[]
  images: string[]
  folderPosts: unknown[]
  opened: number
  unmocked: string[]
}

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })

export async function mockManga(page: Page, opts: { local?: boolean; empty?: boolean } = {}): Promise<State> {
  const local = opts.local ?? true
  const s: State = { calls: [], images: [], folderPosts: [], opened: 0, unmocked: [] }
  await page.route('**/api/**', (route) => {
    s.unmocked.push(`${route.request().method()} ${route.request().url()}`)
    return route.abort()
  })
  await page.route(/\/api\/meta$/, (route) => json(route, { app: 'Baihe Studio', api_version: '0.1', environment: 'test', local }))
  await page.route(/\/api\/auth\/me$/, (route) => json(route, ME.authOff))
  await page.route(/\/api\/diagnostics\/remote-health$/, (route) => json(route, REMOTE_HEALTH_OFF))
  await page.route(/\/api\/assistant\/settings$/, (route) => json(route, { developer_mode: false, engine: null, model: null, engine_choices: [] }))
  await page.route('**/api/events?*', (route) =>
    route.fulfill({ status: 429, json: { error: { code: 'rate_limited', message: 'No stream in this test.' } } }))
  await page.route(/\/api\/notifications$/, (route) => json(route, { items: [] }))
  await page.route(/\/api\/jobs$/, (route) => json(route, { items: [] }))

  await mockSavedComics(page, s, opts)
  return s
}

// Only /api/saved-comics/*, for specs that run the rest of the app against the seeded API.
export async function mockSavedComics(page: Page, s: State, opts: { local?: boolean; empty?: boolean } = {}) {
  const local = opts.local ?? true
  let folder = { folder: 'C:\\Baihe\\data\\saved_comics', custom: false, picked_missing: false }
  const pngs = new Map<number, Buffer>()
  await page.route(/\/api\/saved-comics\/folder(\/open)?$/, (route) => {
    const req = route.request()
    s.calls.push(`${req.method()} ${new URL(req.url()).pathname}`)
    if (!local) return json(route, { error: { code: 'forbidden', message: 'Not allowed.' } }, 403)
    if (req.url().endsWith('/open')) {
      s.opened += 1
      return json(route, { opened: true })
    }
    if (req.method() === 'POST') {
      const body = req.postDataJSON() as { folder: string }
      s.folderPosts.push(body)
      if (body.folder === 'relative') return json(route, { error: { code: 'validation_error', message: 'Use the full path of the folder, starting with its drive.' } }, 422)
      folder = body.folder
        ? { folder: body.folder, custom: true, picked_missing: false }
        : { folder: 'C:\\Baihe\\data\\saved_comics', custom: false, picked_missing: false }
    }
    return json(route, folder)
  })
  await page.route(/\/api\/saved-comics\/series$/, (route) =>
    json(route, opts.empty ? [] : [{ source: SOURCE, series: SERIES, chapter_count: 2, updated_at: Date.now() / 1000 - 120 }]))
  await page.route(/\/api\/saved-comics\/chapters\?/, (route) => {
    const q = new URL(route.request().url()).searchParams
    expect([q.get('source'), q.get('series')]).toEqual([SOURCE, SERIES])
    return json(route, {
      source: SOURCE, series: SERIES,
      chapters: CHAPTERS.map((c, i) => ({ chapter: c, title: c.slice(5), number: i + 1 })),
    })
  })
  await page.route(/\/api\/saved-comics\/pages\?/, (route) => {
    const chapter = new URL(route.request().url()).searchParams.get('chapter')!
    const i = CHAPTERS.indexOf(chapter)
    if (i < 0) return json(route, { error: { code: 'not_found', message: 'No such saved chapter.' } }, 404)
    const count = i === 0 ? 4 : 3
    return json(route, {
      chapter, title: chapter.slice(5), number: i + 1, source: SOURCE, series: SERIES,
      pages: Array.from({ length: count }, (_, k) => ({ page: k + 1, width: W, height: H })),
      prev_chapter: CHAPTERS[i - 1] ?? null, next_chapter: CHAPTERS[i + 1] ?? null,
    })
  })
  await page.route(/\/api\/saved-comics\/page\?/, (route) => {
    const q = new URL(route.request().url()).searchParams
    const n = Number(q.get('page'))
    s.images.push(`${q.get('chapter')}:${n}`)
    let png = pngs.get(n)
    if (!png) {
      png = pagePng(n, W, H, [], false)
      pngs.set(n, png)
    }
    return route.fulfill({ status: 200, contentType: 'image/png', body: png })
  })
}

export const newState = (): State => ({ calls: [], images: [], folderPosts: [], opened: 0, unmocked: [] })

