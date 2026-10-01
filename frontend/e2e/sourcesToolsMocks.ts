import type { Page, Route } from '@playwright/test'

import { NOVEL_PREVIEW, VIDEO_PREVIEW, type ImportMockState } from './sourcesImportMocks'
import type { MockState } from './sourcesMocks'

// Sources tools mocks (SO02 site check, SO03 pasted page source, SO08
// identify media, SO16 pasted-link imports), on top of sourcesMocks.ts and
// sourcesImportMocks.ts. Call last (later routes take precedence).

const PREFLIGHT = {
  kind: 'url_preflight', ok: true, verdict: 'Looks importable as a novel.', permitted: true, reachable: true,
  content_type: 'novel', tier: 'STATIC_HTTP', adapter: null, title: 'Chapter 5', text_chars: 5120, images: 0,
  confidence: 'HIGH', next_link: true, previous_link: false, warnings: [],
  lines: ['Reached the page over STATIC_HTTP.', 'Found a next-chapter link -- the import can follow next chapters from here.'],
  display_url: 'https://novels.example/book/5',
}

const IDENTIFY = {
  kind: 'media_identify', run_id: 'run1', found: true, needs_review: true,
  reason: "Found media resources, but which one is the content isn't clear -- pick one yourself.",
  protection: ['DRM'],
  resources: [
    { index: 0, kind: 'video', role: 'trailer', language: null, label: null, display_url: 'https://cdn.example/t.mp4', downloadable: true },
    { index: 1, kind: 'video', role: 'main', language: null, label: '1080p', display_url: 'https://cdn.example/a.mp4', downloadable: true },
    { index: 2, kind: 'subtitle', role: 'subtitle', language: 'zh', label: null, display_url: 'https://cdn.example/a.vtt', downloadable: false },
  ],
}

export const FULL_RESOURCE = 'https://cdn.example/a.mp4?sig=abc'

const EXTRACTIONS = [
  {
    url: 'https://novels.example/book/5', created_at: Date.now() / 1000 - 300, content_type: 'novel',
    headline: 'Worked: Static HTTP + Deterministic extraction', tier: 'STATIC_HTTP', extraction_tier: 'deterministic',
    llm_calls: 0, cache_hit: false, profile: 'Site profile (novels.example): no saved profile for this site.',
    confidence: 'HIGH',
    access: { authentication: 'NOT_REQUIRED', entitlement: 'UNKNOWN', technical_protection: 'NONE', protection_detail: [] },
    resource_types: ['TEXT'], reason: '', lines: ['Reached the page over STATIC_HTTP.'],
  },
]

interface ToolsMockState {
  preflight: 'none' | 'running' | 'done'
  identify: 'none' | 'running' | 'done'
  pastedPreview: unknown
}

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
const notFound = (route: Route) => json(route, { error: { code: 'not_found', message: 'No such job.' } }, 404)

export async function mockTools(page: Page, s: MockState, m: ImportMockState, over: Partial<ToolsMockState> = {}) {
  const t: ToolsMockState = { preflight: 'none', identify: 'none', pastedPreview: { ...NOVEL_PREVIEW, pasted: true }, ...over }
  const record = (route: Route) => {
    const req = route.request()
    const url = new URL(req.url())
    let body: unknown
    try {
      body = req.postDataJSON()
    } catch {
      body = req.postData()
    }
    s.calls.push({ method: req.method(), path: url.pathname + url.search, body })
    return url
  }

  await page.route(/\/api\/sources\/url\/preflight$/, (route) => {
    record(route)
    t.preflight = 'running'
    return json(route, { job_id: 'sources_url_preflight' })
  })
  await page.route(/\/api\/sources\/jobs\/sources_url_preflight\/result$/, (route) => {
    record(route)
    if (t.preflight === 'none') return notFound(route)
    if (t.preflight === 'running') {
      t.preflight = 'done'
      return json(route, { job_id: 'sources_url_preflight', status: 'running', progress: 0.1, message: 'Checking the site...', result: null })
    }
    return json(route, { job_id: 'sources_url_preflight', status: 'done', progress: 1, message: null, result: PREFLIGHT })
  })

  await page.route(/\/api\/sources\/url\/preview-pasted$/, (route) => {
    record(route)
    return json(route, t.pastedPreview)
  })
  await page.route(/\/api\/sources\/url\/import-pasted$/, (route) => {
    record(route)
    const body = route.request().postDataJSON() as { drama_id: number }
    m.importJob = 'running'
    m.importKind = 'url'
    m.importCancelRequested = false
    return json(route, { job_id: `sourceimport_${body.drama_id}` })
  })

  await page.route(/\/api\/sources\/url\/identify-media$/, (route) => {
    record(route)
    t.identify = 'running'
    return json(route, { job_id: 'sources_url_identify' })
  })
  await page.route(/\/api\/sources\/jobs\/sources_url_identify\/result$/, (route) => {
    record(route)
    if (t.identify === 'none') return notFound(route)
    if (t.identify === 'running') {
      t.identify = 'done'
      return json(route, { job_id: 'sources_url_identify', status: 'running', progress: 0.1, message: 'Looking for media...', result: null })
    }
    return json(route, { job_id: 'sources_url_identify', status: 'done', progress: 1, message: null, result: IDENTIFY })
  })
  await page.route(/\/api\/sources\/url\/identify-media\/resource\?.*$/, (route) => {
    const url = record(route)
    if (url.searchParams.get('run_id') !== 'run1') return notFound(route)
    return json(route, { run_id: 'run1', index: Number(url.searchParams.get('index')), resource_url: FULL_RESOURCE })
  })
  await page.route(/\/api\/sources\/url\/extractions(\?.*)?$/, (route) => {
    record(route)
    return json(route, EXTRACTIONS)
  })
  return t
}

export { NOVEL_PREVIEW, VIDEO_PREVIEW }
