import type { Page, Route } from '@playwright/test'

import type { MockState } from './sourcesMocks'

// Import mocks (S-4/S-5), on top of sourcesMocks.ts: the paste-a-link
// preview (R1), novel URL import (R2), chapter import (R3), Track (R4), the
// video download (R5) and the drama list/create they use. Call after
// mockSources (later routes take precedence over its guards). Every route
// here is fulfilled or aborted; nothing falls through to a server.

export const DRAMAS = [
  { id: 11, title_en: 'Heaven Novel', title_zh: '天官', media_type: 'novel', content_mode: 'novel_narration' },
  { id: 12, title_en: 'Alpha Comic', title_zh: null, media_type: 'manhua', content_mode: null },
  { id: 13, title_en: 'Radio Play', title_zh: null, media_type: 'audio_drama', content_mode: 'audio_drama' },
  { id: 14, title_en: 'Stream VOD', title_zh: null, media_type: 'streamer_vod', content_mode: 'streamer_vod' },
].map((d) => ({
  author: null, studio: null, director: null, voice_actors: null, status: 'not started', source_language: 'zh',
  series_id: null, translation_engine: null, custom_tags: [], created_at: null, updated_at: null, ...d,
}))

export function urlPreview(over: Record<string, unknown> = {}) {
  return {
    kind: 'url_preview', content_type: 'comic', route: 'chapter', platform: 'Alpha Comics', title: 'Heaven Book 1',
    chapter: 'Chapter 2', chapter_id: 'c2', language: 'zh', chapter_count: 124, adapter: 'alpha', series_id: 'a0',
    text_length: null, image_count: 18, notes: ['Pages load one by one (paced).'], display_url: 'https://alpha.example/a/c2',
    ...over,
  }
}

export const NOVEL_PREVIEW = urlPreview({
  content_type: 'novel', route: 'page', platform: 'Some Novel Site', title: 'Heaven Official, Chapter 5', chapter: 'Chapter 5',
  chapter_id: null, chapter_count: null, adapter: null, series_id: null, text_length: 5120, image_count: null,
  notes: ['Found the chapter text by its layout.'], display_url: 'https://novels.example/book/5',
})

export const VIDEO_PREVIEW = urlPreview({
  content_type: 'video', route: 'video', platform: 'Video Site', title: 'Episode 1', chapter: null, chapter_id: null,
  chapter_count: null, adapter: null, series_id: null, image_count: null, notes: [], display_url: 'https://video.example/watch',
})

export function chapterImportResult(over: Record<string, unknown> = {}) {
  return {
    kind: 'chapter_import',
    chapters: [
      { chapter_id: 'c1', title: 'Chapter 1', outcome: 'imported', pages: 20 },
      { chapter_id: 'c2', title: 'Chapter 2', outcome: 'skipped' },
      { chapter_id: 'c3', title: 'Chapter 3', outcome: 'failed', error: 'The site took too long to answer.' },
    ],
    imported_count: 1, skipped_count: 1, failed_count: 1, cancelled: false, handoff: null,
    ...over,
  }
}

export interface ImportMockState {
  preview: 'none' | 'running' | 'done' | 'handoff'
  previewBody: unknown
  // Keep a running preview running until the test flips it.
  previewHold: boolean
  // The sourceimport_<drama> job (one at a time is enough here).
  importJob: 'none' | 'running' | 'done' | 'cancelled'
  importBody: unknown
  importHold: boolean
  dramas: unknown[]
  hasAudio: boolean
  urlmedia: 'none' | 'running' | 'done'
  // POST download-url answers 403 (the viewer is not at the PC).
  downloadForbidden: boolean
}

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })

const notFound = (route: Route) => json(route, { error: { code: 'not_found', message: 'No such job.' } }, 404)

export async function mockImports(page: Page, s: MockState, over: Partial<ImportMockState> = {}): Promise<ImportMockState> {
  const m: ImportMockState = {
    preview: 'none', previewBody: urlPreview(), previewHold: false, importJob: 'none', importBody: chapterImportResult(),
    importHold: false, dramas: DRAMAS, hasAudio: false, urlmedia: 'none', downloadForbidden: false, ...over,
  }
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
  const guard = (route: Route) => {
    record(route)
    s.unmocked.push(`${route.request().method()} ${route.request().url()}`)
    return route.abort()
  }
  // Guards for the other areas these flows touch.
  await page.route(/\/api\/dramas(\/.*)?(\?.*)?$/, guard)
  await page.route(/\/api\/media\/.*/, guard)
  await page.route(/\/api\/library\/.*/, guard)
  await page.route(/\/api\/jobs\/(sourceimport|urlmedia)_[^/]+(\/.*)?$/, guard)

  await page.route(/\/api\/library\/dramas(\?.*)?$/, (route) => {
    record(route)
    return json(route, { items: m.dramas, count: m.dramas.length })
  })
  await page.route(/\/api\/dramas$/, (route) => {
    if (route.request().method() !== 'POST') return guard(route)
    record(route)
    const body = route.request().postDataJSON() as Record<string, unknown>
    const d = { ...DRAMAS[0], id: 21, title_en: null, title_zh: null, content_mode: null, ...body }
    m.dramas = [d, ...m.dramas]
    return json(route, { ...d, summary: null, genre: null, has_audio: false, has_novel_reference: false, has_cover_art: false })
  })
  await page.route(/\/api\/media\/dramas\/\d+\/status$/, (route) => {
    const url = record(route)
    const id = Number(url.pathname.split('/')[4])
    return json(route, { drama_id: id, has_audio: m.hasAudio, has_source_video: false, upload_max_mb: 2048 })
  })

  // R1 preview: one fixed job; a start while it runs is a 409 naming it.
  await page.route(/\/api\/sources\/url\/preview$/, (route) => {
    record(route)
    if (m.preview === 'running') {
      return json(route, { error: { code: 'conflict', message: 'Already running.', details: { job_id: 'sources_url_preview' } } }, 409)
    }
    m.preview = 'running'
    return json(route, { job_id: 'sources_url_preview' })
  })
  await page.route(/\/api\/sources\/jobs\/sources_url_preview\/result$/, (route) => {
    record(route)
    if (m.preview === 'none') return notFound(route)
    // Without a hold the first look already finds it done (keeps slow machines quick).
    if (m.preview === 'running' && !m.previewHold) m.preview = 'done'
    if (m.preview === 'running') {
      return json(route, { job_id: 'sources_url_preview', status: 'running', progress: 0.4, message: 'Checking the link…', result: null })
    }
    if (m.preview === 'handoff') {
      return json(route, {
        error: {
          code: 'conflict', message: 'Browser check.',
          details: { reason: 'CHALLENGE', handoff: true, open_url: 'https://alpha.example/a/c2' },
        },
      }, 409)
    }
    return json(route, { job_id: 'sources_url_preview', status: 'done', progress: 1, message: null, result: m.previewBody })
  })

  // R2 / R3 start the per-drama import job.
  const startImport = (route: Route) => {
    record(route)
    const body = route.request().postDataJSON() as { drama_id: number }
    if (m.importJob === 'running') {
      return json(route, {
        error: { code: 'conflict', message: 'An import is already running for this drama.', details: { job_id: `sourceimport_${body.drama_id}` } },
      }, 409)
    }
    m.importJob = 'running'
    return json(route, { job_id: `sourceimport_${body.drama_id}` })
  }
  await page.route(/\/api\/sources\/url\/import$/, startImport)
  await page.route(/\/api\/sources\/(alpha|beta)\/import$/, startImport)
  await page.route(/\/api\/sources\/jobs\/sourceimport_\d+\/result$/, (route) => {
    const url = record(route)
    const id = url.pathname.split('/')[4]
    if (m.importJob === 'none') return notFound(route)
    if (m.importJob === 'running' && !m.importHold) m.importJob = 'done'
    if (m.importJob === 'running') {
      return json(route, { job_id: id, status: 'running', progress: 0.5, message: 'Chapter 2 of 3…', result: null })
    }
    if (m.importJob === 'cancelled') return json(route, { job_id: id, status: 'cancelled', progress: 0.5, message: null, result: null })
    return json(route, { job_id: id, status: 'done', progress: 1, message: null, result: m.importBody })
  })
  await page.route(/\/api\/jobs\/sourceimport_\d+\/cancel$/, (route) => {
    const url = record(route)
    m.importJob = 'cancelled'
    m.importHold = false
    return json(route, { job_id: url.pathname.split('/')[3], cancel_requested: true })
  })

  // R4 Track (untrack stays guarded, as in mockSources).
  await page.route(/\/api\/sources\/tracked$/, (route) => {
    const req = route.request()
    if (req.method() === 'GET') {
      record(route)
      return json(route, s.tracked)
    }
    const body = req.postDataJSON() as { source: string; series_id: string; tracked: boolean; drama_id?: number }
    if (!body.tracked) return guard(route)
    record(route)
    s.tracked = [
      ...s.tracked,
      {
        source: body.source, series_id: body.series_id, title: 'Heaven Book 1', url: '', drama_id: body.drama_id ?? null,
        last_checked: null, last_check_error: null,
      },
    ]
    return json(route, s.tracked)
  })

  // R5 video download (PC only) and its job.
  await page.route(/\/api\/media\/dramas\/\d+\/download-url$/, (route) => {
    const url = record(route)
    if (m.downloadForbidden) return json(route, { error: { code: 'forbidden', message: 'PC only.' } }, 403)
    m.urlmedia = 'running'
    return json(route, { job_id: `urlmedia_${url.pathname.split('/')[4]}` })
  })
  await page.route(/\/api\/jobs\/urlmedia_\d+$/, (route) => {
    const url = record(route)
    const id = url.pathname.split('/').pop()!
    if (m.urlmedia === 'none') return notFound(route)
    const running = m.urlmedia === 'running'
    if (running) m.urlmedia = 'done'
    return json(route, {
      job_id: id, status: running ? 'running' : 'done', progress: running ? 0.3 : 1,
      message: running ? 'Downloading…' : 'Downloaded.', error: null, description: null, gpu_touching: false,
      started_at: 1, finished_at: running ? null : 2, updated_at: 1, outcome: running ? null : 'ok',
    })
  })
  return m
}
