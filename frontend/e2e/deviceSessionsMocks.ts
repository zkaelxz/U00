import type { Page, Request } from '@playwright/test'

import { ME, type MeBody } from './authMocks'

// Settings > Signed-in devices. /api/auth/me, /api/auth/sessions* and the
// Settings reads a household member makes are mocked with a small stateful
// fixture; a catch-all aborts (and records) any other non-GET /api call, so
// nothing is written to the seeded library.

type Device = {
  id: number; device: string; created_at: number; last_seen_at: number; expires_at: number
  ip_prefix: string; current: boolean
}

const now = () => Date.now() / 1000

function devices(): Device[] {
  const t = now()
  return [
    { id: 11, device: 'Safari on iPhone', created_at: t - 3 * 86400, last_seen_at: t - 10, expires_at: t + 27 * 86400, ip_prefix: '203.0.113', current: true },
    { id: 12, device: 'Chrome on Android', created_at: t - 9 * 86400, last_seen_at: t - 2 * 3600, expires_at: t + 21 * 86400, ip_prefix: '198.51.100', current: false },
    { id: 13, device: 'Edge on Windows', created_at: t - 20 * 86400, last_seen_at: t - 5 * 86400, expires_at: t + 10 * 86400, ip_prefix: '2001:db8:1::/48', current: false },
  ]
}

interface DevicesMock {
  list: Device[]
  sent: Request[]
  unmocked: string[]
  /** When set, the next revoke of this id answers with this error. */
  refuse: { id: number; status: number; code: string; message: string } | null
}

export async function mockDevices(page: Page, me: MeBody = ME.signedIn): Promise<DevicesMock> {
  const s: DevicesMock = { list: devices(), sent: [], unmocked: [], refuse: null }
  await page.route('**/api/**', (route) => {
    const r = route.request()
    if (r.method() === 'GET') return route.fallback()
    s.unmocked.push(`${r.method()} ${r.url()}`)
    return route.abort()
  })
  await page.route('**/api/auth/me', (route) => route.fulfill({ json: me }))
  // A household member: the admin Settings cards stay hidden.
  await page.route('**/api/settings', (route) =>
    route.fulfill({ status: 403, json: { error: { code: 'forbidden', message: 'Not allowed.' } } }))
  await page.route('**/api/sharing/share-by-default', (route) => route.fulfill({ json: { share_by_default: false } }))
  await page.route('**/api/events**', (route) =>
    route.fulfill({ status: 429, json: { error: { code: 'rate_limited', message: 'No stream in this test.' } } }))
  await page.route('**/api/auth/sessions**', (route) => {
    const r = route.request()
    const path = new URL(r.url()).pathname
    if (r.method() === 'GET' && path === '/api/auth/sessions') {
      return route.fulfill({ json: { sessions: s.list, idle_timeout_days: 14, absolute_timeout_days: 30 } })
    }
    if (r.method() !== 'POST') return route.abort()
    s.sent.push(r)
    if (path === '/api/auth/sessions/revoke-others') {
      const n = s.list.filter((d) => !d.current).length
      s.list = s.list.filter((d) => d.current)
      // Like the server: this device's session is rotated, so both cookies are set again.
      return route.fulfill({
        json: { revoked: n },
        headers: { 'set-cookie': 'baihe_csrf=csrf-rotated; Path=/; SameSite=Strict' },
      })
    }
    const m = path.match(/^\/api\/auth\/sessions\/(\d+)\/revoke$/)
    if (!m) return route.abort()
    const id = Number(m[1])
    if (s.refuse?.id === id) {
      const { status, code, message } = s.refuse
      s.list = s.list.filter((d) => d.id !== id)
      return route.fulfill({ status, json: { error: { code, message } } })
    }
    s.list = s.list.filter((d) => d.id !== id)
    return route.fulfill({ json: { revoked: 1 } })
  })
  return s
}
