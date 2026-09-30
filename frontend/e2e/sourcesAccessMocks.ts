import type { Page, Route } from '@playwright/test'

import { DRAMAS } from './sourcesImportMocks'
import type { MockState } from './sourcesMocks'

// Mocks for Check now, the tracked-series drama link, sign-in, per-tier
// tests and the proxy, on top of sourcesMocks.ts (call after mockSources:
// later routes take precedence over its guards). Nothing reaches a server.

export const TRACKED = [
  { source: 'beta', series_id: 'b0', title: 'Heaven Book 1', url: 'https://beta.example/b', drama_id: null, last_checked: null, last_check_error: null },
]

export interface AccessMockState {
  check: 'none' | 'running' | 'done'
  signin: 'none' | 'running' | 'done'
  tier: 'none' | 'running' | 'done'
  tierBody: Record<string, unknown>
  proxyConfigured: boolean
  hasSignin: boolean
}

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
const notFound = (route: Route) => json(route, { error: { code: 'not_found', message: 'No such Sources job.' } }, 404)

export async function mockAccess(page: Page, s: MockState, over: Partial<AccessMockState> = {}): Promise<AccessMockState> {
  const m: AccessMockState = {
    check: 'none', signin: 'none', tier: 'none', proxyConfigured: false, hasSignin: true,
    tierBody: { kind: 'tier_test', source: 'beta', tier: 'static', ok: true, reason: null, detail: '' },
    ...over,
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
  }

  await page.route(/\/api\/library\/dramas(\?.*)?$/, (route) => json(route, { items: DRAMAS, count: DRAMAS.length }))

  await page.route(/\/api\/sources\/check-now$/, (route) => {
    record(route)
    m.check = 'running'
    return json(route, { job_id: 'sources_chapter_check' })
  })
  await page.route(/\/api\/sources\/jobs\/sources_chapter_check\/result$/, (route) => {
    record(route)
    if (m.check === 'none') return notFound(route)
    if (m.check === 'running') {
      m.check = 'done'
      return json(route, { job_id: 'sources_chapter_check', status: 'running', progress: 0.5, message: 'Checking Heaven Book 1 (1/1)', result: null })
    }
    return json(route, {
      job_id: 'sources_chapter_check', status: 'done', progress: 1, message: null,
      result: { checked: 1, new: 1, errors: {}, queued: [] },
    })
  })
  await page.route(/\/api\/sources\/tracked\/drama$/, (route) => {
    record(route)
    const body = route.request().postDataJSON() as { source: string; series_id: string; drama_id: number | null }
    s.tracked = (s.tracked as typeof TRACKED).map((t) =>
      t.source === body.source && t.series_id === body.series_id ? { ...t, drama_id: body.drama_id } : t)
    return json(route, s.tracked)
  })

  await page.route(/\/api\/sources\/beta\/signin\/open$/, (route) => {
    record(route)
    m.signin = 'running'
    return json(route, { job_id: 'sources_signin_beta' })
  })
  await page.route(/\/api\/sources\/jobs\/sources_signin_beta\/result$/, (route) => {
    record(route)
    if (m.signin === 'none') return notFound(route)
    if (m.signin === 'running') {
      m.signin = 'done'
      return json(route, { job_id: 'sources_signin_beta', status: 'running', progress: 0.1, message: 'Waiting', result: null })
    }
    m.hasSignin = true
    return json(route, {
      job_id: 'sources_signin_beta', status: 'done', progress: 1, message: null,
      result: { kind: 'signin', source: 'beta', ok: true, message: 'Signed in -- this page is visible.', lines: ['Signed-in browser: works'], has_saved_signin: true },
    })
  })
  await page.route(/\/api\/sources\/beta\/signin\/forget$/, (route) => {
    record(route)
    m.hasSignin = false
    return json(route, { source: 'beta', forgotten: true, has_saved_signin: false })
  })
  await page.route(/\/api\/sources\/beta\/tier-test$/, (route) => {
    record(route)
    m.tier = 'running'
    return json(route, { job_id: 'sources_tiertest_beta' })
  })
  await page.route(/\/api\/sources\/jobs\/sources_tiertest_beta\/result$/, (route) => {
    record(route)
    if (m.tier === 'none') return notFound(route)
    if (m.tier === 'running') {
      m.tier = 'done'
      return json(route, { job_id: 'sources_tiertest_beta', status: 'running', progress: 0.1, message: 'Testing...', result: null })
    }
    return json(route, { job_id: 'sources_tiertest_beta', status: 'done', progress: 1, message: null, result: m.tierBody })
  })
  await page.route(/\/api\/sources\/settings\/proxy$/, (route) => {
    record(route)
    const body = route.request().postDataJSON() as { url: string }
    m.proxyConfigured = !!body.url.trim()
    return json(route, {
      pace_min_delay: 3, pace_max_delay: 8, max_concurrent: 1, max_retries: 3,
      session_break_min_requests: 8, session_break_max_requests: 20,
      session_break_min_delay: 30, session_break_max_delay: 90,
      cache_mode: 'keep_originals', cache_max_mb: 0, check_interval_hours: 24,
      auto_queue_new_chapters: false, demo_source_enabled: false, extraction_diagnostics: false,
      proxy_configured: m.proxyConfigured,
      cache_modes: ['none', 'temporary', 'keep_originals', 'keep_translated', 'keep_both'],
      cache: { entries: 120, bytes: 45_200_000 },
    })
  })
  return m
}
