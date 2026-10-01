import type { Page, Route } from '@playwright/test'

import { ME } from './authMocks'

// Shared page.route mocks for the Discover specs. Every /api/discover/*
// call is mocked (the lookup helpers would reach the internet and an AI
// engine, and every spec shares one seeded library); a guard aborts and
// records anything under /api/discover that nothing mocks.

const TITLES = [
  {
    id: 1, title_original: '女将军和长公主', title_en: 'The General and the Princess', author: 'Mo Xi',
    tags: 'baihe, historical', summary_en: 'A general and a princess.', summary_original: '', source_name: 'manual',
    source_url: 'https://example.cn/t/1', language: 'zh', media_type: 'audio_drama', created_at: '2026-09-01',
  },
  {
    id: 2, title_original: '月の庭', title_en: 'Moon Garden', author: '', tags: '', summary_en: '', summary_original: '',
    source_name: 'manual', source_url: '', language: 'ja', media_type: 'novel', created_at: '2026-09-02',
  },
]

const ENGINES = [
  { name: 'claude', label: 'Claude', free: false, models: null, key_configured: true },
  { name: 'nllb', label: 'NLLB', free: false, models: null, key_configured: true },
  { name: 'ollama', label: 'Ollama', free: true, models: null, key_configured: true },
]

const BULK_RESULT = {
  entries: [
    { entry_id: 'r-0', title: '长公主', author: 'A', tags: 'gl', source_url: 'https://www.jjwxc.net/tag.php', has_audio_drama: true },
    { entry_id: 'r-1', title: '青梅', author: 'B', tags: '', source_url: 'https://www.jjwxc.net/tag.php', has_audio_drama: false },
    { entry_id: 'r-2', title: '雪夜', author: '', tags: '', source_url: 'https://www.jjwxc.net/tag.php', has_audio_drama: false },
  ],
  pages: [
    { url: 'https://www.jjwxc.net/tag.php?page=1', ok: true, needs_manual: false, count: 3, message: '' },
    { url: 'https://www.jjwxc.net/tag.php?page=2', ok: false, needs_manual: true, count: 0, message: 'The page needs JavaScript.' },
  ],
  source_label: 'jjwxc_baihe_tag',
}

interface Call {
  method: string
  path: string
  body: unknown
  headers: Record<string, string>
}

interface DiscoverMock {
  titles: typeof TITLES
  engines: typeof ENGINES
  local: boolean
  bulk: 'none' | 'running' | 'done'
  nav: 'none' | 'running' | 'done'
  importConflict: boolean
  calls: Call[]
  unmocked: string[]
}

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })

export async function mockDiscover(page: Page, over: Partial<DiscoverMock> = {}): Promise<DiscoverMock> {
  const s: DiscoverMock = {
    titles: [...TITLES], engines: ENGINES, local: true, bulk: 'none', nav: 'none', importConflict: false,
    calls: [], unmocked: [], ...over,
  }
  const record = (route: Route) => {
    const req = route.request()
    const url = new URL(req.url())
    let body: unknown
    try {
      body = req.postDataJSON()
    } catch {
      body = req.postData()
    }
    s.calls.push({ method: req.method(), path: url.pathname + url.search, body, headers: req.headers() })
    return url
  }

  // Guard first: later routes take precedence.
  await page.route(/\/api\/discover\/.*/, (route) => {
    record(route)
    s.unmocked.push(`${route.request().method()} ${route.request().url()}`)
    return route.abort()
  })
  await page.route(/\/api\/auth\/me$/, (route) => json(route, ME.authOff))
  await page.route(/\/api\/meta$/, (route) =>
    json(route, { app: 'Baihe Studio', api_version: '0.1', environment: 'test', local: s.local }),
  )
  await page.route(/\/api\/translate\/engines$/, (route) => json(route, { items: s.engines }))

  await page.route(/\/api\/discover\/titles(\?.*)?$/, (route) => {
    const url = record(route)
    if (route.request().method() === 'POST') {
      const b = route.request().postDataJSON()
      const t = { ...TITLES[1], ...b, id: 100 + s.titles.length, summary_original: '', created_at: '2026-09-29' }
      s.titles.push(t)
      return json(route, t, 201)
    }
    const q = (url.searchParams.get('q') ?? '').toLowerCase()
    const lang = url.searchParams.get('language') ?? ''
    const rows = s.titles.filter(
      (t) =>
        (!q || [t.title_original, t.title_en, t.author].some((v) => (v ?? '').toLowerCase().includes(q))) &&
        (!lang || t.language === lang),
    )
    return json(route, { titles: rows, total: s.titles.length })
  })
  await page.route(/\/api\/discover\/titles\/seed$/, (route) => {
    record(route)
    const added = TITLES.filter((t) => !s.titles.some((x) => x.id === t.id))
    s.titles.push(...added)
    return json(route, { added: added.length, total: s.titles.length })
  })
  await page.route(/\/api\/discover\/titles\/\d+\/delete$/, (route) => {
    const url = record(route)
    if (!s.local) return json(route, { error: { code: 'forbidden', message: 'PC only.' } }, 403)
    const id = Number(url.pathname.split('/')[4])
    s.titles = s.titles.filter((t) => t.id !== id)
    return json(route, { deleted: true, id })
  })
  await page.route(/\/api\/discover\/titles\/\d+\/import-to-library$/, (route) => {
    record(route)
    if (s.importConflict) {
      return json(route, { error: { code: 'conflict', message: 'This title is already in your Library.', details: { drama_id: 3 } } }, 409)
    }
    return json(route, { id: 42, title_en: 'x' }, 201)
  })
  await page.route(/\/api\/discover\/platforms(\?.*)?$/, (route) => {
    record(route)
    return json(route, {
      platforms: [
        { name: 'JJWXC (晋江文学城)', url: 'https://www.jjwxc.net', region: 'China', language: 'zh', content_types: ['novel'], notes: 'Web novels.' },
        { name: 'Bad link site', url: 'javascript:alert(1)', region: 'Nowhere', language: 'zh', content_types: ['novel'], notes: '' },
      ],
    })
  })
  await page.route(/\/api\/discover\/search-links(\?.*)?$/, (route) => {
    const url = record(route)
    const q = url.searchParams.get('q') ?? ''
    return json(route, {
      links: [
        { site: 'General web search', url: `https://www.google.com/search?q=${encodeURIComponent(q)}`, kind: 'web', note: 'Broad search' },
        { site: 'JJWXC (晋江文学城)', url: 'https://www.jjwxc.net/search?q=x', kind: 'web', note: 'Search JJWXC' },
      ],
    })
  })
  await page.route(/\/api\/discover\/translate-query$/, (route) => {
    record(route)
    const b = route.request().postDataJSON()
    return json(route, { query: b.q, translated: '女将军', engine: b.engine ?? 'claude' })
  })
  await page.route(/\/api\/discover\/baihehub-search$/, (route) => {
    record(route)
    return json(route, { results: [], fallback_url: 'https://baihehub.com/search?q=x' })
  })
  await page.route(/\/api\/discover\/import-suggestion$/, (route) => {
    record(route)
    return json(route, {
      suggestion: { title_zh: '雪夜', title_en: 'Snow Night', author: 'Lin', summary: 'Two girls, one winter.' },
      found: true, needs_manual: false, message: '',
    })
  })
  await page.route(/\/api\/discover\/bulk-extract$/, (route) => {
    record(route)
    s.bulk = 'running'
    return json(route, { job_id: 'discover_bulk_extract', started: true })
  })
  // DI07 manual fallback: pasted listing text runs the same bulk job.
  await page.route(/\/api\/discover\/bulk-extract\/pasted$/, (route) => {
    record(route)
    s.bulk = 'running'
    return json(route, { job_id: 'discover_bulk_extract', started: true })
  })
  await page.route(/\/api\/discover\/bulk-extract\/result$/, (route) => {
    record(route)
    if (s.bulk === 'none') return json(route, { error: { code: 'not_found', message: 'No such job.' } }, 404)
    if (s.bulk === 'running') {
      return json(route, { job_id: 'discover_bulk_extract', status: 'running', progress: 0.5, message: 'Read 1 of 2 pages', result: null })
    }
    return json(route, { job_id: 'discover_bulk_extract', status: 'done', progress: 1, message: 'Done', result: BULK_RESULT })
  })
  await page.route(/\/api\/discover\/bulk-commit$/, (route) => {
    record(route)
    const n = route.request().postDataJSON().entries.length
    return json(route, { added: n - 1, skipped: 1, ids: [] })
  })
  await page.route(/\/api\/discover\/navigation-help$/, (route) => {
    record(route)
    s.nav = 'done'
    return json(route, { job_id: 'discover_navigation_help', started: true })
  })
  await page.route(/\/api\/discover\/navigation-help\/result$/, (route) => {
    record(route)
    if (s.nav === 'none') return json(route, { error: { code: 'not_found', message: 'No such job.' } }, 404)
    return json(route, {
      job_id: 'discover_navigation_help', status: 'done', progress: 1, message: 'Done',
      result: {
        labels: { 首页: 'Home', 广播剧: 'Audio dramas' },
        steps: '1. Click **广播剧** (Audio dramas) in the top menu.\n2. Use the search box.',
        needs_manual: false, message: '',
      },
    })
  })
  return s
}

export const posts = (s: DiscoverMock, suffix: string) =>
  s.calls.filter((c) => c.method === 'POST' && c.path.endsWith(suffix))
