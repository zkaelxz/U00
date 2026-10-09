import type { Page, Route } from '@playwright/test'

import { SOURCES } from './sourcesMocks'
import type { MockState } from './sourcesMocks'

// A source whose Static and Browser tests failed and whose "works only with the
// browser extension" marker the person can set (alpha). Call after mockSources:
// later routes win over its guards.

export const MARKED_AT = Date.UTC(2026, 9, 8) / 1000

interface ExtensionMockState {
  on: boolean
  note: string
  worksWithout: boolean
}

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })

function detail(m: ExtensionMockState) {
  const tier = { tested: false, ok: false, reason: null, detail: '', at: null }
  return {
    ...SOURCES[0],
    extension_only: m.on,
    extension_marked_at: m.on ? MARKED_AT : null,
    extension_note: m.on ? m.note : '',
    extension_works_without: m.on && m.worksWithout,
    status: 'UNTESTED', technical_status: 'UNRESOLVED', access_method: null, content_access_status: 'UNKNOWN',
    authentication_required: 'UNKNOWN', purchase_required: 'UNKNOWN', technical_protection: 'UNKNOWN',
    automation_permission: 'UNKNOWN', ai_ml_use: 'UNKNOWN',
    tiers: {
      STATIC_HTTP: { tested: true, ok: false, reason: 'EMPTY_SPA_SHELL', detail: '', at: 1_700_000_000 },
      RENDERED_BROWSER: { tested: true, ok: false, reason: 'JAVASCRIPT_REQUIRED', detail: '', at: 1_700_000_000 },
      AUTHENTICATED_BROWSER: tier,
      USER_ASSISTED_BROWSER: tier,
      OFFICIAL_API: tier,
    },
    technical: {}, terms: {}, terms_enforced: false,
    health_detail: {
      light: 'green', consecutive_failures: 0, last_success: null, last_failure: null, last_error_type: null,
      last_error_category: null, last_error: null, last_latency: null, unavailable_until: null, retry_after: null,
    },
  }
}

export async function mockExtensionOnly(page: Page, s: MockState, over: Partial<ExtensionMockState> = {}) {
  const m: ExtensionMockState = { on: false, note: '', worksWithout: false, ...over }
  await page.route(/\/api\/sources$/, (route) =>
    json(route, SOURCES.map((x) => (x.name === 'alpha' ? { ...x, extension_only: m.on } : x))))
  await page.route(/\/api\/sources\/alpha$/, (route) => json(route, detail(m)))
  // Opening Details polls the source's tier-test job; nothing has run.
  await page.route(/\/api\/sources\/jobs\/sources_tiertest_alpha\/result$/, (route) =>
    json(route, { job_id: '', status: 'idle', progress: 0, message: '', result: null }))
  await page.route(/\/api\/sources\/alpha\/extension-only$/, (route) => {
    const body = route.request().postDataJSON() as { extension_only: boolean; note?: string }
    s.calls.push({ method: 'POST', path: new URL(route.request().url()).pathname, body })
    m.on = body.extension_only
    m.note = body.note ?? ''
    if (!m.on) m.worksWithout = false
    const d = detail(m)
    return json(route, {
      extension_only: d.extension_only, extension_marked_at: d.extension_marked_at,
      extension_note: d.extension_note, extension_works_without: d.extension_works_without,
    })
  })
  return m
}
