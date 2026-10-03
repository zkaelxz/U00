import type { Page, Route } from '@playwright/test'

// Shared page.route mocks for the Sources specs. A real search or series
// fetch would hit the internet (paced), and every spec shares one seeded
// library, so every job start, job result and settings write is mocked
// here; guards abort (and record) any Sources or job-cancel call nothing mocks.

export const SOURCES = [
  {
    name: 'alpha', display_name: 'Alpha Comics', content_types: ['manhua'], languages: ['zh'],
    supports: { search: true, get_series: true, get_chapters: true, get_pages: true, download_page: true, get_chapter_text: false, get_audio_url: false, login: false },
    import_supported: true, auth_supported: false, supports_adult_toggle: true, enabled: true, adult_enabled: false,
    health: 'green', has_saved_signin: false,
  },
  {
    name: 'beta', display_name: 'Beta Novels', content_types: ['novel'], languages: ['zh'],
    supports: { search: true, get_series: true, get_chapters: true, get_pages: false, download_page: false, get_chapter_text: true, get_audio_url: false, login: false },
    import_supported: true, auth_supported: true, supports_adult_toggle: false, enabled: true, adult_enabled: false,
    health: 'yellow', has_saved_signin: true,
  },
  {
    name: 'gamma', display_name: 'Gamma Video', content_types: ['video'], languages: ['zh'],
    supports: { search: false, get_series: false, get_chapters: false, get_pages: false, download_page: false, get_chapter_text: false, get_audio_url: false, login: false },
    import_supported: false, auth_supported: false, supports_adult_toggle: false, enabled: true, adult_enabled: false,
    health: 'red', has_saved_signin: false,
  },
]

export const SETTINGS = {
  pace_min_delay: 3, pace_max_delay: 8, max_concurrent: 1, max_retries: 3,
  session_break_min_requests: 8, session_break_max_requests: 20,
  session_break_min_delay: 30, session_break_max_delay: 90,
  cache_mode: 'keep_originals', cache_max_mb: 0, check_interval_hours: 24,
  auto_queue_new_chapters: false, demo_source_enabled: false, extraction_diagnostics: false,
  proxy_configured: false,
  cache_modes: ['none', 'temporary', 'keep_originals', 'keep_translated', 'keep_both'],
  cache: { entries: 120, bytes: 45_200_000 },
}

export function searchResult(n = 3) {
  return {
    kind: 'search',
    query: 'Heaven',
    cancelled: false,
    results: Array.from({ length: n }, (_, i) => ({
      title: `Heaven Book ${i + 1}`,
      key: `k${i}`,
      sources: i === 0 ? ['alpha', 'beta'] : ['alpha'],
      entries: [
        { source: 'alpha', series_id: `a${i}`, title: `Heaven Book ${i + 1}`, url: 'https://alpha.example/a', cover_url: 'https://alpha.example/c.jpg' },
        ...(i === 0 ? [{ source: 'beta', series_id: 'b0', title: 'Heaven Book 1', url: 'https://beta.example/b', cover_url: '' }] : []),
      ],
    })),
    errors: {
      beta: { status: 503, code: 'dependency_unavailable', message: 'x', details: { reason: 'UNAVAILABLE', retry_after: 240 } },
      alpha: { status: 400, code: 'unsupported_operation', message: 'x', details: { reason: 'CONTENT_HIDDEN' } },
    },
    per_source_counts: { alpha: n, beta: 1 },
  }
}


function seriesResult(chapters = 124, series_id = 'a0', title = 'Heaven Book 1', links?: unknown[]) {
  return {
    kind: 'series',
    source: 'alpha',
    series_id,
    info: {
      title, url: 'https://alpha.example/a', cover_url: 'https://alpha.example/c.jpg',
      authors: ['Mo Xiang'], description: 'A long description. '.repeat(20), genres: ['xianxia'],
      status: 'ongoing', content_type: 'manhua', language: 'zh',
      ...(links ? { links } : {}),
    },
    chapters: Array.from({ length: chapters }, (_, i) => ({
      chapter_id: `c${i + 1}`, title: `Chapter ${i + 1}`, group: i < chapters - 2 ? 'Main' : 'Extras', url: 'https://alpha.example/c',
    })),
  }
}

// A work's posted EPUB links as the service sends them (no query string).
export const SERIES_LINKS = [
  { label: '百度网盘 (Baidu Pan)', url: 'https://pan.baidu.com/s/1UW8fzsl6WfJ1RRIXRt_MPw', password: 'roh1' },
  { label: '蓝奏云 (Lanzou)', url: 'https://wwasa.lanzoue.com/b0188mxnyb', password: '' },
]

function sourceDetail(summary: (typeof SOURCES)[number]) {
  const tier = { tested: false, ok: false, reason: null, detail: '', at: null }
  return {
    ...summary,
    status: 'UNTESTED', technical_status: 'UNRESOLVED', access_method: null, content_access_status: 'IMAGES',
    authentication_required: 'UNKNOWN', purchase_required: 'UNKNOWN', technical_protection: 'UNKNOWN',
    automation_permission: 'UNKNOWN', ai_ml_use: 'UNKNOWN',
    tiers: { STATIC_HTTP: { ...tier, tested: true, ok: true }, RENDERED_BROWSER: tier },
    technical: {},
    terms: { robots_txt: 'Crawl-delay: 10.', tos: 'Not reviewed.', tos_prohibited: false },
    terms_enforced: false,
    health_detail: {
      light: summary.health, last_success: null, last_latency: null, unavailable_until: null,
      ...(summary.health === 'yellow'
        ? { consecutive_failures: 1, last_failure: 1_700_000_000, last_error_type: 'SERVER_ERROR', last_error_category: 'site_down' }
        : { consecutive_failures: 0, last_failure: null, last_error_type: null, last_error_category: null }),
      retry_after: summary.health === 'red' ? 240 : null,
    },
  }
}


const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })

const notFound = (route: Route) => json(route, { error: { code: 'not_found', message: 'No such Sources job.' } }, 404)

export interface MockState {
  calls: { method: string; path: string; body: unknown }[]
  search: 'none' | 'running' | 'done'
  searchBody: unknown
  // The alpha series job (the id is per source: sources_series_alpha).
  series: 'none' | 'running' | 'done'
  // Which series the job holds; its result is seriesResult(124, seriesId, seriesTitle).
  seriesId: string
  seriesTitle: string
  // Download links the series posts (info.links), if any.
  seriesLinks?: unknown[]
  // Keep a running series job running (no auto-finish on the next poll).
  seriesHold: boolean
  tracked: unknown[]
  notifications: unknown[]
  local: boolean
  // Calls that nothing mocks: aborted, and must stay empty.
  unmocked: string[]
}

// Every request matched here is fulfilled or aborted; nothing falls through
// to the real server (a mocked POST must never reach it).
export async function mockSources(page: Page, over: Partial<MockState> = {}): Promise<MockState> {
  const s: MockState = {
    calls: [], search: 'none', searchBody: searchResult(), series: 'none', seriesId: 'a0', seriesTitle: 'Heaven Book 1',
    seriesHold: false, tracked: [], notifications: [], local: true, unmocked: [], ...over,
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

  // Guards first: later routes take precedence, so these only catch what nothing else mocks.
  const guard = (route: Route) => {
    record(route)
    s.unmocked.push(`${route.request().method()} ${route.request().url()}`)
    return route.abort()
  }
  await page.route(/\/api\/sources\/.*/, guard)
  await page.route(/\/api\/jobs\/[^/]+\/cancel$/, guard)

  await page.route(/\/api\/meta$/, (route) => json(route, { app: 'Baihe Studio', api_version: '0.1', environment: 'test', local: s.local }))
  await page.route(/\/api\/sources(\?.*)?$/, (route) => {
    record(route)
    return json(route, SOURCES)
  })
  await page.route(/\/api\/sources\/tracked$/, (route) => {
    record(route)
    return route.request().method() === 'GET' ? json(route, s.tracked) : guard(route)
  })
  await page.route(/\/api\/sources\/notifications(\?.*)?$/, (route) => {
    record(route)
    return json(route, s.notifications)
  })
  await page.route(/\/api\/sources\/settings$/, (route) => {
    record(route)
    return route.request().method() === 'GET' ? json(route, SETTINGS) : guard(route)
  })
  await page.route(/\/api\/sources\/profiles$/, (route) => {
    record(route)
    return json(route, [])
  })
  await page.route(/\/api\/sources\/(alpha|beta|gamma)$/, (route) => {
    const url = record(route)
    const name = url.pathname.split('/').pop()
    return json(route, sourceDetail(SOURCES.find((x) => x.name === name)!))
  })
  await page.route(/\/api\/sources\/(alpha|beta|gamma)\/attempts(\?.*)?$/, (route) => {
    record(route)
    return json(route, [])
  })
  await page.route(/\/api\/sources\/search$/, (route) => {
    record(route)
    s.search = 'running'
    return json(route, { job_id: 'sources_search' })
  })
  // Like the server: one series job per source; a second start while one runs is a 409.
  await page.route(/\/api\/sources\/[^/]+\/series$/, (route) => {
    record(route)
    if (s.series === 'running') {
      return json(route, {
        error: { code: 'conflict', message: 'A request like this is already running.', details: { job_id: 'sources_series_alpha' } },
      }, 409)
    }
    const body = route.request().postDataJSON() as { series_id: string }
    s.series = 'running'
    s.seriesId = body.series_id
    return json(route, { job_id: 'sources_series_alpha' })
  })
  await page.route(/\/api\/sources\/jobs\/sources_search\/result$/, (route) => {
    record(route)
    if (s.search === 'none') return notFound(route)
    if (s.search === 'running') return json(route, { job_id: 'sources_search', status: 'running', progress: 0.1, message: null, result: null })
    return json(route, { job_id: 'sources_search', status: 'done', progress: 1, message: null, result: s.searchBody })
  })
  await page.route(/\/api\/sources\/jobs\/sources_series_[^/]+\/result$/, (route) => {
    record(route)
    if (s.series === 'none') return notFound(route)
    if (s.series === 'running') {
      if (!s.seriesHold) s.series = 'done' // the next poll finishes
      return json(route, {
        job_id: 'sources_series_alpha', status: 'running', progress: 0.2, message: 'Loading the series...', result: null,
        source: 'alpha', series_id: s.seriesId,
      })
    }
    return json(route, {
      job_id: 'sources_series_alpha', status: 'done', progress: 1, message: null,
      source: 'alpha', series_id: s.seriesId,
      result: seriesResult(124, s.seriesId, s.seriesTitle, s.seriesLinks),
    })
  })
  // The paste-a-link box looks for an earlier preview on load: none here
  // (sourcesImportMocks.ts overrides this for the import flows).
  await page.route(/\/api\/sources\/jobs\/sources_url_preview\/result$/, (route) => {
    record(route)
    return notFound(route)
  })
  // New chapters looks for an earlier "Check now" run on load: none here
  // (sourcesAccessMocks.ts overrides this).
  // Save as CBZ (sourcesImportMocks.ts mocks a run): nothing stored here.
  await page.route(/\/api\/sources\/jobs\/sources_save\/result$/, (route) =>
    route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ error: { code: 'not_found', message: 'No such job.' } }) }),
  )
  await page.route(/\/api\/sources\/jobs\/sources_chapter_check\/result$/, (route) => {
    record(route)
    return notFound(route)
  })
  // The site check (SO02) and identify media (SO08) look for an earlier run
  // on mount, and Source settings lists recent pasted-link imports (SO16):
  // none here (sourcesToolsMocks.ts overrides these).
  await page.route(/\/api\/sources\/jobs\/sources_url_(preflight|identify)\/result$/, (route) => {
    record(route)
    return notFound(route)
  })
  await page.route(/\/api\/sources\/url\/extractions(\?.*)?$/, (route) => {
    record(route)
    return json(route, [])
  })
  await page.route(/\/api\/jobs\/sources_search\/cancel$/, (route) => {
    record(route)
    s.search = 'done'
    return json(route, { job_id: 'sources_search', cancel_requested: true })
  })
  await page.route(/\/api\/jobs\/sources_series_alpha\/cancel$/, (route) => {
    record(route)
    s.series = 'none'
    s.seriesHold = false
    return json(route, { job_id: 'sources_series_alpha', cancel_requested: true })
  })
  return s
}

export const posted = (s: MockState, path: string) => s.calls.filter((c) => c.method === 'POST' && c.path === path)
