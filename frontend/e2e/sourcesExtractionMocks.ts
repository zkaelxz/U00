import type { Page, Route } from '@playwright/test'

import type { ImportMockState } from './sourcesImportMocks'
import { urlPreview } from './sourcesImportMocks'
import type { MockState } from './sourcesMocks'

// Pasted-URL extraction mocks (parity SO09, SO06, SO10), on top of
// sourcesMocks.ts and sourcesImportMocks.ts: the AI engine list, the comic
// URL import and the Review extraction routes. Call after mockImports. The
// import job itself is mockImports' sourceimport_<drama> job; these set the
// result it answers with (m.urlImportBody). Every route is fulfilled.

export const COMIC_PAGE_PREVIEW = urlPreview({
  content_type: 'comic', route: 'page', platform: 'Some Comic Site', title: 'Chapter 5', chapter: 'Chapter 5', chapter_id: null,
  chapter_count: null, adapter: null, series_id: null, image_count: 5, notes: [], display_url: 'https://comics.example/read/5',
})

export function novelReview(over: Record<string, unknown> = {}) {
  return {
    kind: 'extraction_review', drama_id: 11, revision: 'r1', content_type: 'novel', why: 'asked',
    display_url: 'https://novels.example/book/5',
    confidence: {
      overall: { bucket: 'MEDIUM', score: 0.62 },
      fields: [
        { field: 'content', bucket: 'MEDIUM', score: 0.62, checks: ['a comment block may be included'], value: null },
        { field: 'chapter_title', bucket: 'HIGH', score: 0.9, checks: [], value: 'Chapter 5' },
      ],
    },
    report: {
      headline: 'Worked: Plain page fetch + Deterministic extraction', lines: ['Deterministic extraction found the chapter.'],
      llm_calls: 0, cache_hit: false, profile: 'Site profile (novels.example): no saved profile for this site.',
      pending_profile: { bucket: 'MEDIUM' },
    },
    can_save_profile: false,
    novel: {
      text_preview: 'Xie Lian walked down the mountain.\nComments: great chapter!', char_count: 5120, chapter_title: 'Chapter 5',
      containers: [
        { selector: '#content', chars: 5120, preview: 'Xie Lian walked down the mountain.', exclusions: [{ selector: '.comments', preview: 'Comments: great chapter!' }] },
        { selector: 'main', chars: 6400, preview: 'Home Chapter 5 Xie Lian', exclusions: [] },
      ],
      content_selector: '#content', exclude_selectors: [],
      headings: [{ id: 'b0', text: 'Chapter 5' }], title_block: 'b0',
      links: [{ id: 'L0', text: 'Next chapter', url: 'https://novels.example/book/6' }, { id: 'L1', text: 'Previous chapter', url: 'https://novels.example/book/4' }],
      next_link: 'L0', previous_link: null, number_from: 'title',
    },
    comic: null,
    ...over,
  }
}

// A novel import that followed next-chapter links: three pages read.
export function followReview(over: Record<string, unknown> = {}) {
  return novelReview({
    why: 'follow',
    follow: {
      stop: 'no_next',
      pages: [
        { id: 0, title: 'Chapter 5', char_count: 5120, host: 'novels.example' },
        { id: 1, title: 'Chapter 6', char_count: 4800, host: 'novels.example' },
        { id: 2, title: 'Chapter 7', char_count: 5000, host: 'novels.example' },
      ],
    },
    ...over,
  })
}

const img = (id: number, name: string, role: string, page: number, reason = '') => ({
  id, display_url: `https://img.comics.example/5/${name}`, attr: 'src', width: role === 'icon' ? 48 : 800,
  height: role === 'icon' ? 48 : 1200, role, page, reason, has_image: true,
})

export function comicReview(over: Record<string, unknown> = {}) {
  return {
    ...novelReview(),
    content_type: 'comic', why: 'low_confidence', display_url: 'https://comics.example/read/5', novel: null,
    confidence: { overall: { bucket: 'LOW', score: 0.4 }, fields: [{ field: 'page_images', bucket: 'LOW', score: 0.4, checks: ['one image is a different shape'], value: null }] },
    report: { ...novelReview().report, pending_profile: null },
    comic: {
      images: [img(0, 'logo.png', 'icon', 0, 'too small to be a page (48x48)'), img(1, '001.png', 'content', 1),
        img(2, '002.png', 'content', 2), img(3, 'banner.png', 'content', 3)],
      roles: ['content', 'cover', 'thumbnail', 'ad', 'recommendation', 'icon', 'duplicate', 'other'],
      page_count: 3,
    },
    ...over,
  }
}

// A 1x1 PNG.
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==', 'base64')

export interface ExtractionMockState {
  review: Record<string, unknown> | null
  // What the next rerun answers with.
  rerunBody: Record<string, unknown> | null
  // The next rerun answers 409 (the review changed elsewhere).
  stale: boolean
}

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })

export async function mockExtraction(page: Page, s: MockState, m: ImportMockState, over: Partial<ExtractionMockState> = {}) {
  const x: ExtractionMockState = { review: null, rerunBody: null, stale: false, ...over }
  const record = (route: Route) => {
    const req = route.request()
    const url = new URL(req.url())
    let body: unknown = undefined
    try {
      body = req.postDataJSON()
    } catch {
      body = req.postData()
    }
    s.calls.push({ method: req.method(), path: url.pathname, body })
    return url
  }
  await page.route(/\/api\/sources\/url\/ai-engines$/, (route) => {
    record(route)
    return json(route, { engines: ['claude', 'gemini', 'ollama'], default: 'claude', free: ['ollama'] })
  })
  const start = (route: Route) => {
    record(route)
    const body = route.request().postDataJSON() as { drama_id?: number }
    const id = body.drama_id ?? Number(new URL(route.request().url()).pathname.split('/')[4])
    m.importJob = 'running'
    m.importKind = 'url'
    m.importCancelRequested = false
    return json(route, { job_id: `sourceimport_${id}` })
  }
  await page.route(/\/api\/sources\/url\/import-comic$/, start)
  await page.route(/\/api\/sources\/dramas\/\d+\/extraction$/, (route) => {
    record(route)
    if (!x.review) return json(route, { error: { code: 'not_found', message: 'No review.' } }, 404)
    return json(route, x.review)
  })
  await page.route(/\/api\/sources\/dramas\/\d+\/extraction\/rerun-(novel|comic)$/, (route) => {
    record(route)
    if (x.stale) {
      x.stale = false
      return json(route, { error: { code: 'conflict', message: 'This review changed since it was loaded. Reload it and try again.' } }, 409)
    }
    x.review = x.rerunBody ?? x.review
    return json(route, x.review)
  })
  await page.route(/\/api\/sources\/dramas\/\d+\/extraction\/(save|approve)-profile$/, (route) => {
    const url = record(route)
    if (url.pathname.endsWith('approve-profile') && x.review) {
      x.review = { ...x.review, report: { ...(x.review.report as object), pending_profile: null } }
    }
    return json(route, { domain: 'novels.example', kind: 'novel', version: 2, replaces: 1 })
  })
  await page.route(/\/api\/sources\/dramas\/\d+\/extraction\/import$/, (route) => {
    const r = x.review as {
      content_type: string; novel?: { char_count: number }; comic?: { page_count: number }
      follow?: { pages: { id: number; char_count: number }[] } | null
    } | null
    const chosen = (route.request().postDataJSON() as { pages?: number[] }).pages
    const pages = r?.follow ? r.follow.pages.filter((p) => !chosen || chosen.includes(p.id)) : null
    m.urlImportBody = r?.content_type === 'comic'
      ? { kind: 'review_import', content_type: 'comic', pages_added: r.comic?.page_count ?? 0 }
      : pages
        ? { kind: 'review_import', content_type: 'novel', char_count: pages.reduce((n, p) => n + p.char_count, 0), pages_imported: pages.length }
        : { kind: 'review_import', content_type: 'novel', char_count: r?.novel?.char_count ?? 0 }
    return start(route)
  })
  await page.route(/\/api\/sources\/dramas\/\d+\/extraction\/images\/\d+$/, (route) => {
    record(route)
    return route.fulfill({ status: 200, contentType: 'image/png', body: PNG })
  })
  return x
}
