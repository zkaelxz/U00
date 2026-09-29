import type { Page, Route } from '@playwright/test'

// Shared page.route mocks for the sign-in specs. The seeded e2e API runs
// with auth off (and may not have /api/auth/* yet), so every /api/auth/*
// call is mocked here. Other GETs are passed explicitly to the seeded API
// (the pages behind the gate need data); any other unmocked call -- a
// POST/DELETE nothing here expects -- is aborted and recorded. Nothing
// ever reaches Google: /api/auth/login is fulfilled with a stand-in page.

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

export interface AuthMockState {
  me: MeBody
  /** When set, /me waits for this before answering. */
  meGate: Promise<void> | null
  loginUrls: string[]
  logoutHeaders: Record<string, string>[]
  /** GET paths (pathname) answered with a 401 instead of reaching the seeded API. */
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
    if (req.method() === 'GET' && !path.startsWith('/api/auth/')) {
      if (s.unauthorizedPaths.has(path)) return json(route, 401, { error: { code: 'unauthorized', message: 'Please sign in.' } })
      return route.continue()
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
