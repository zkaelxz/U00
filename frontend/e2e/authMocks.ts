import type { Page, Route } from '@playwright/test'

// Shared page.route mocks for the sign-in specs. Every /api/* request is
// fulfilled here or aborted -- none is continued to the seeded API (which
// runs with auth off). /api/auth/* is mocked; the few GETs the Library
// page behind the gate makes get small fixed bodies (GET_FIXTURES); any
// other call is aborted and recorded in `unmocked`, which each spec
// asserts is empty. Nothing ever reaches Google: /api/auth/login is
// fulfilled with a stand-in page.

export interface UserBody {
  id: number | null
  email: string | null
  display_name: string | null
  is_admin: boolean
  is_local_owner: boolean
}

export const USER: UserBody = {
  id: 7,
  email: 'kae.household.member@example.com',
  display_name: 'Kae',
  is_admin: false,
  is_local_owner: false,
}

export interface MeBody {
  auth_enabled: boolean
  signed_in: boolean
  sign_in_configured: boolean
  zone: string | null
  user: UserBody | null
  permissions: string[]
}

export const ME = {
  authOff: { auth_enabled: false, signed_in: true, sign_in_configured: false, zone: 'pc', user: { id: null, email: null, display_name: 'This PC', is_admin: true, is_local_owner: true }, permissions: ['admin.settings'] } as MeBody,
  signedOut: { auth_enabled: true, signed_in: false, sign_in_configured: true, zone: 'internet', user: null, permissions: [] } as MeBody,
  notConfigured: { auth_enabled: true, signed_in: false, sign_in_configured: false, zone: 'internet', user: null, permissions: [] } as MeBody,
  signedIn: { auth_enabled: true, signed_in: true, sign_in_configured: true, zone: 'internet', user: USER, permissions: ['library.read', 'lines.edit'] } as MeBody,
}

const DRAMA = {
  id: 1, title_zh: '魔道祖师', title_en: 'Grandmaster of Demonic Cultivation', author: null, studio: null,
  director: null, voice_actors: null, status: 'translated', source_language: 'zh', media_type: 'audio_drama',
  content_mode: 'audio_drama', series_id: null, translation_engine: 'claude', custom_tags: [],
  created_at: '2026-09-29T12:00:00', updated_at: '2026-09-29T12:00:00',
}
const EMPTY = { items: [] }
const OFF_CHECK = { state: 'off', message: 'Remote access is off.' }
export const REMOTE_HEALTH_OFF = {
  state: 'off', message: 'Remote access is off.', checked_at: null, since: null,
  certificate: { ...OFF_CHECK, days_left: null }, ddns: { ...OFF_CHECK, configured: false }, listener: OFF_CHECK,
}

/** What the app shell and the Library page GET, keyed by pathname. */
const GET_FIXTURES: Record<string, unknown> = {
  '/api/health': { status: 'ok' },
  '/api/meta': { app: 'Baihe Studio', api_version: '0.1', environment: 'production', local: false },
  '/api/library/dramas': { items: [DRAMA], count: 1 },
  '/api/library/stats': {
    total_dramas: 1, by_status: { translated: 1 }, by_media_type: { audio_drama: 1 }, total_lines: 0, translated_lines: 0,
    usage: { input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, estimated_cost_usd: 0, call_count: 0 },
  },
  '/api/library/recent': { items: [{ id: 1, title_en: DRAMA.title_en, title_zh: DRAMA.title_zh, status: 'translated', updated_at: DRAMA.updated_at, media_type: null }] },
  '/api/library/costs': EMPTY,
  '/api/library/series': EMPTY,
  '/api/library/history': EMPTY,
  '/api/library/continue': EMPTY,
  '/api/library/filter-options': { studios: [], authors: [], voice_actors: [], custom_tags: [] },
  '/api/library/presets': EMPTY,
  '/api/library/voice-bank': EMPTY,
  // Library tools lists the saved manga series.
  '/api/saved-comics/series': [],
  // The header bell (every page) polls this.
  '/api/notifications': EMPTY,
  // The header Jobs button (every page) reads this.
  '/api/jobs': EMPTY,
  // The remote-access banner reads this on the PC only (these specs are remote); off here.
  '/api/diagnostics/remote-health': REMOTE_HEALTH_OFF,
  // Settings > Remote access (PC only, so not asked by these remote specs).
  '/api/diagnostics/remote-health/ip-check': { configured: false },
  // The Library admin panel's per-person backup picker.
  '/api/admin/users': { users: [] },
  // Settings > App updates (PC only): a source checkout, never checked.
  '/api/system/update': {
    current: null, installed: false, latest: null, update_available: false, notes: '', installer_name: null,
    size: null, checked_at: null, check_error: null, release_lookup: 'unchecked', download: 'idle', downloaded_bytes: 0, download_error: null,
    verified: false, verified_version: null, verified_name: null, can_install: false, auto_check: false,
    custom_source: false,
  },
  // Settings > Signed-in devices, for a signed-in person: just this device.
  '/api/auth/sessions': {
    sessions: [{ id: 1, device: 'Chrome on Windows', created_at: 1759000000, last_seen_at: 1759000000, expires_at: 1761592000, ip_prefix: '203.0.113', current: true }],
    idle_timeout_days: 14,
    absolute_timeout_days: 30,
  },
}

// The Library admin panel polls its last backup/export job and artifact;
// with none made yet the real API answers 404 not_found, as here.
const GET_NOT_FOUND = /^\/api\/(jobs\/library_(backup|db_backup|user_backup|export_zip)|library\/admin\/artifacts\/(backup|database|user_backup|export)\/info)$/

export interface AuthMockState {
  me: MeBody
  /** When set, /me waits for this before answering. */
  meGate: Promise<void> | null
  loginUrls: string[]
  logoutHeaders: Record<string, string>[]
  /** GET paths (pathname) answered with a 401 instead of their fixture. */
  unauthorizedPaths: Set<string>
  unmocked: string[]
}

const json = (route: Route, status: number, body: unknown) =>
  route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })

export async function mockAuth(page: Page, me: MeBody): Promise<AuthMockState> {
  const s: AuthMockState = { me, meGate: null, loginUrls: [], logoutHeaders: [], unauthorizedPaths: new Set(), unmocked: [] }

  // Guard first: Playwright tries the most recently added route first, so this runs last.
  await page.route((u) => u.pathname.startsWith('/api/'), async (route) => {
    const req = route.request()
    const path = new URL(req.url()).pathname
    if (req.method() === 'GET' && s.unauthorizedPaths.has(path)) {
      return json(route, 401, { error: { code: 'unauthorized', message: 'Please sign in.' } })
    }
    // No push stream (GET /api/events): pages poll, as these specs expect.
    if (req.method() === 'GET' && path === '/api/events') {
      return json(route, 429, { error: { code: 'rate_limited', message: 'No stream in this test.' } })
    }
    if (req.method() === 'GET' && Object.prototype.hasOwnProperty.call(GET_FIXTURES, path)) {
      return json(route, 200, GET_FIXTURES[path])
    }
    if (req.method() === 'GET' && GET_NOT_FOUND.test(path)) {
      return json(route, 404, { error: { code: 'not_found', message: 'Nothing yet.' } })
    }
    s.unmocked.push(`${req.method()} ${path}`)
    return route.abort()
  })

  await page.route((u) => u.pathname === '/api/auth/me', async (route) => {
    if (s.meGate) await s.meGate
    return json(route, 200, s.me)
  })

  await page.route((u) => u.pathname === '/api/auth/login', async (route) => {
    s.loginUrls.push(route.request().url())
    return route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><title>Stand-in for Google</title><h1>Stand-in for Google</h1>' })
  })

  await page.route((u) => u.pathname === '/api/auth/logout', async (route) => {
    if (route.request().method() !== 'POST') return route.abort()
    s.logoutHeaders.push(await route.request().allHeaders())
    s.me = ME.signedOut
    return json(route, 200, { signed_out: true })
  })

  return s
}

/** Save a screenshot when SIGNIN_SCREENS_DIR is set (review evidence; not an assertion). */
export async function maybeScreenshot(page: Page, name: string): Promise<void> {
  const dir = process.env.SIGNIN_SCREENS_DIR
  if (dir) await page.screenshot({ path: `${dir.replace(/\/$/, '')}/${name}.png`, fullPage: true })
}
