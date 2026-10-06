import { deflateSync } from 'node:zlib'

import type { Page, Route } from '@playwright/test'

import { ME, REMOTE_HEALTH_OFF } from './authMocks'

// Shared page.route mocks for the comic viewer specs (routes under
// /api/scanlate/dramas/{id}).
// A catch-all aborts (and records) every /api call nothing here mocks, so no
// request ever falls through to the seeded server. Page images are PNGs
// generated here: a tinted page, a big page number and white speech bubbles
// where the text boxes are (filled with grey "text" in the typeset variant).

// ---- PNG generation (no dependencies) ----

const CRC_TABLE = (() => {
  const t = new Uint32Array(256)
  for (let n = 0; n < 256; n++) {
    let c = n
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
    t[n] = c >>> 0
  }
  return t
})()

function crc32(buf: Buffer): number {
  let c = 0xffffffff
  for (const b of buf) c = CRC_TABLE[(c ^ b) & 0xff] ^ (c >>> 8)
  return (c ^ 0xffffffff) >>> 0
}

function chunk(type: string, data: Buffer): Buffer {
  const len = Buffer.alloc(4)
  len.writeUInt32BE(data.length)
  const td = Buffer.concat([Buffer.from(type, 'ascii'), data])
  const crc = Buffer.alloc(4)
  crc.writeUInt32BE(crc32(td))
  return Buffer.concat([len, td, crc])
}

type RGB = [number, number, number]

function encodePng(w: number, h: number, px: Uint8Array): Buffer {
  const raw = Buffer.alloc((w * 3 + 1) * h)
  for (let y = 0; y < h; y++) {
    raw[y * (w * 3 + 1)] = 0
    Buffer.from(px.buffer, px.byteOffset + y * w * 3, w * 3).copy(raw, y * (w * 3 + 1) + 1)
  }
  const ihdr = Buffer.alloc(13)
  ihdr.writeUInt32BE(w, 0)
  ihdr.writeUInt32BE(h, 4)
  ihdr[8] = 8
  ihdr[9] = 2
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(raw)),
    chunk('IEND', Buffer.alloc(0)),
  ])
}

// 3x5 digits
const DIGITS: Record<string, string[]> = {
  '0': ['111', '101', '101', '101', '111'], '1': ['010', '110', '010', '010', '111'],
  '2': ['111', '001', '111', '100', '111'], '3': ['111', '001', '111', '001', '111'],
  '4': ['101', '101', '111', '001', '001'], '5': ['111', '100', '111', '001', '111'],
  '6': ['111', '100', '111', '101', '111'], '7': ['111', '001', '010', '010', '010'],
  '8': ['111', '101', '111', '101', '111'], '9': ['111', '101', '111', '001', '111'],
}

const HUES: RGB[] = [[214, 226, 245], [245, 222, 214], [220, 240, 222], [238, 226, 246], [246, 238, 210], [212, 236, 238]]

interface Box { x: number; y: number; w: number; h: number }

export function pagePng(n: number, w: number, h: number, boxes: Box[], typeset: boolean): Buffer {
  const px = new Uint8Array(w * h * 3)
  const bg = HUES[(n - 1) % HUES.length]
  const fill = (x0: number, y0: number, x1: number, y1: number, c: RGB) => {
    for (let y = Math.max(0, y0); y < Math.min(h, y1); y++)
      for (let x = Math.max(0, x0); x < Math.min(w, x1); x++) px.set(c, (y * w + x) * 3)
  }
  fill(0, 0, w, h, bg)
  // Panel borders.
  const ink: RGB = [40, 40, 48]
  const panelH = Math.floor(h / 3)
  for (let i = 0; i < 3; i++) {
    const y0 = i * panelH + 12
    fill(12, y0, w - 12, y0 + 6, ink)
    fill(12, y0 + panelH - 24, w - 12, y0 + panelH - 18, ink)
    fill(12, y0, 18, y0 + panelH - 18, ink)
    fill(w - 18, y0, w - 12, y0 + panelH - 18, ink)
  }
  // Page number, bottom right.
  const s = Math.max(8, Math.floor(w / 40))
  const digits = String(n)
  digits.split('').forEach((d, i) => {
    const ox = w - 40 - (digits.length - i) * 4 * s
    const oy = h - 40 - 5 * s
    DIGITS[d].forEach((row, ry) => row.split('').forEach((bit, rx) => {
      if (bit === '1') fill(ox + rx * s, oy + ry * s, ox + (rx + 1) * s, oy + (ry + 1) * s, ink)
    }))
  })
  // Bubbles.
  for (const b of boxes) {
    fill(b.x - 4, b.y - 4, b.x + b.w + 4, b.y + b.h + 4, ink)
    fill(b.x, b.y, b.x + b.w, b.y + b.h, [255, 255, 255])
    if (typeset) {
      for (let ly = b.y + 14; ly + 8 < b.y + b.h - 10; ly += 20) fill(b.x + 14, ly, b.x + b.w - 14, ly + 8, [90, 90, 100])
    }
  }
  return encodePng(w, h, px)
}

// ---- Mock data ----

export interface ComicMockOptions {
  id: number
  title: string
  mediaType: string
  pageCount: number
  width: number
  height: number
  // Saved progress (C4); null makes C4 answer 403.
  lastPage: number | null
  // GET progress (C4) answers 500 (a server fault, not a refusal).
  progressFails: boolean
  // Page indexes (0-based, i.e. ordinal - 1) that have a typeset image.
  rendered: number[]
  // Every image answers 403 (no media.stream).
  imagesForbidden: boolean
  // Extra region text for page 1 (e.g. markup that must stay text).
  firstPageText: string | null
  // Hold the page list back this long (ms), to see the bar before the pages arrive.
  pagesDelayMs: number
}

export interface ComicMockState {
  opts: ComicMockOptions
  calls: { method: string; path: string; body: unknown }[]
  // Image requests as "<pageId>:<variant>".
  images: string[]
  progressPosts: number[]
  unmocked: string[]
}

export const pageIdOf = (id: number, ordinal: number) => id * 100 + ordinal + 1

function boxesFor(w: number, h: number): Box[] {
  return [
    { x: Math.round(w * 0.55), y: Math.round(h * 0.06), w: Math.round(w * 0.32), h: Math.round(h * 0.1) },
    { x: Math.round(w * 0.1), y: Math.round(h * 0.4), w: Math.round(w * 0.36), h: Math.round(h * 0.09) },
    { x: Math.round(w * 0.5), y: Math.round(h * 0.74), w: Math.round(w * 0.34), h: Math.round(h * 0.1) },
  ]
}

const LINES = [
  ['Where did you go last night?', '你昨晚去哪儿了？'],
  ['Nowhere. I was reading.', '哪儿也没去，我在看书。'],
  ['Then why is the lamp still warm?', '那灯怎么还是热的？'],
]

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })

export async function mockComic(page: Page, over: Partial<ComicMockOptions> = {}): Promise<ComicMockState> {
  const opts: ComicMockOptions = {
    id: 7, title: 'Moonlit Courtyard', mediaType: 'manhua', pageCount: 8, width: 800, height: 1200,
    lastPage: 1, progressFails: false, rendered: [], imagesForbidden: false, firstPageText: null, pagesDelayMs: 0, ...over,
  }
  const s: ComicMockState = { opts, calls: [], images: [], progressPosts: [], unmocked: [] }
  const pngCache = new Map<string, Buffer>()
  const record = (route: Route) => {
    const req = route.request()
    const url = new URL(req.url())
    let body: unknown = undefined
    try {
      body = req.postDataJSON()
    } catch {
      body = req.postData()
    }
    s.calls.push({ method: req.method(), path: url.pathname + url.search, body })
    return url
  }

  // Catch-all first: later routes take precedence, so this only sees what nothing else mocks.
  await page.route('**/api/**', (route) => {
    record(route)
    s.unmocked.push(`${route.request().method()} ${route.request().url()}`)
    return route.abort()
  })

  const { id } = opts
  const root = `/api/scanlate/dramas/${id}`
  await page.route(/\/api\/meta$/, (route) => json(route, { app: 'Baihe Studio', api_version: '0.1', environment: 'test', local: true }))
  // Sign-in off, on the PC (the app asks before rendering any page).
  await page.route(/\/api\/auth\/me$/, (route) => json(route, ME.authOff))
  // The app shell's remote-access banner (PC only): remote access off.
  await page.route(/\/api\/diagnostics\/remote-health$/, (route) => json(route, REMOTE_HEALTH_OFF))
  // The header asks whether to show the Assistant link (Developer Mode off).
  await page.route(/\/api\/assistant\/settings$/, (route) => json(route, { developer_mode: false, engine: null, model: null, engine_choices: [] }))

  // No push stream (GET /api/events): the page polls, as these specs expect.
  await page.route('**/api/events?*', (route) =>
    route.fulfill({ status: 429, json: { error: { code: 'rate_limited', message: 'No stream in this test.' } } }))
  // The header bell (every page) polls this; not part of the comic flow.
  await page.route(/\/api\/notifications$/, (route) => json(route, { items: [] }))
  // The header Jobs button (every page) reads this; not part of the comic flow.
  await page.route(/\/api\/jobs$/, (route) => json(route, { items: [] }))
  await page.route(new RegExp(`/api/library/dramas/${id}$`), (route) => {
    record(route)
    return json(route, {
      id, title_zh: '月下庭院', title_en: opts.title, author: null, studio: null, director: null, voice_actors: null,
      status: 'translated', source_language: 'zh', media_type: opts.mediaType, content_mode: null, series_id: null,
      translation_engine: null, custom_tags: [], created_at: null, updated_at: null, summary: null, genre: null,
      publication_status: null, chapter_count: null, narration_language: null, author_romanized: null,
      studio_romanized: null, director_romanized: null, voice_actors_romanized: null, series_instructions: null,
      has_audio: false, has_novel_reference: false, has_cover_art: false,
    })
  })
  await page.route(new RegExp(`${root}/pages$`), async (route) => {
    record(route)
    if (opts.pagesDelayMs) await new Promise((r) => setTimeout(r, opts.pagesDelayMs))
    return json(route, {
      drama_id: id,
      media_type: opts.mediaType,
      // As the backend: manga reads a page at a time, everything else scrolls.
      reading_mode_default: opts.mediaType.toLowerCase() === 'manga' ? 'paged' : 'vertical',
      page_count: opts.pageCount,
      pages: Array.from({ length: opts.pageCount }, (_, i) => ({
        id: pageIdOf(id, i), ordinal: i + 1, width: opts.width, height: opts.height,
        has_rendered: opts.rendered.includes(i), has_regions: true, image_version: 1700000000 + i,
      })),
      chapters: [],
    })
  })
  await page.route(new RegExp(`${root}/pages/\\d+/image(\\?.*)?$`), (route) => {
    const url = record(route)
    const pid = Number(url.pathname.split('/').at(-2))
    const variant = url.searchParams.get('variant') ?? 'original'
    const ordinal = pid - id * 100 - 1
    s.images.push(`${pid}:${variant}`)
    if (opts.imagesForbidden) {
      return json(route, { error: { code: 'forbidden', message: 'Not allowed.' } }, 403)
    }
    if (ordinal < 0 || ordinal >= opts.pageCount || (variant === 'rendered' && !opts.rendered.includes(ordinal))) {
      return json(route, { error: { code: 'not_found', message: 'Not found.' } }, 404)
    }
    const key = `${ordinal}:${variant}`
    let png = pngCache.get(key)
    if (!png) {
      png = pagePng(ordinal + 1, opts.width, opts.height, boxesFor(opts.width, opts.height), variant === 'rendered')
      pngCache.set(key, png)
    }
    return route.fulfill({ status: 200, contentType: 'image/png', body: route.request().method() === 'HEAD' ? '' : png })
  })
  await page.route(new RegExp(`${root}/pages/\\d+/regions$`), (route) => {
    const url = record(route)
    const pid = Number(url.pathname.split('/').at(-2))
    const n = pid - id * 100
    const boxes = boxesFor(opts.width, opts.height)
    return json(route, {
      page_id: pid, width: opts.width, height: opts.height,
      // Sent out of order: the viewer sorts by idx.
      regions: [2, 0, 1].map((k) => ({
        idx: k, ...boxes[k], kind: 'bubble', source_text: LINES[k][1],
        translated_text: n === 1 && k === 0 && opts.firstPageText ? opts.firstPageText : `${LINES[k][0]} (p${n})`,
      })),
    })
  })
  await page.route(new RegExp(`${root}/progress$`), (route) => {
    record(route)
    if (route.request().method() === 'POST') {
      const p = (route.request().postDataJSON() as { page: number }).page
      s.progressPosts.push(p)
      return json(route, { last_page: p, percent_complete: (p / opts.pageCount) * 100 })
    }
    if (opts.progressFails) return json(route, { error: { code: 'internal', message: 'Something went wrong.' } }, 500)
    if (opts.lastPage === null) return json(route, { error: { code: 'forbidden', message: 'Not allowed.' } }, 403)
    return json(route, { last_page: opts.lastPage, percent_complete: (opts.lastPage / opts.pageCount) * 100 })
  })
  return s
}

// Set COMIC_SCREENS_DIR to also save screenshots; unset in CI, so those tests skip.
export const SHOTS_DIR = process.env.COMIC_SCREENS_DIR ?? ''
