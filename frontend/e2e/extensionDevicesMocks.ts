import type { Page, Request } from '@playwright/test'

import { type MeBody } from './authMocks'
import { mockDevices } from './deviceSessionsMocks'

// Settings > Browser extension devices. Builds on the signed-in devices mock
// (me, Settings reads, a catch-all that aborts and records any other write)
// and adds a small stateful /api/auth/device-tokens and
// /api/admin/device-tokens. No real token is ever created.

type Token = {
  id: number; label: string; created_at: number; last_used_at: number | null; last_used_ip_prefix: string
  expires_at: number | null; revoked_at: number | null; status: 'active' | 'revoked' | 'expired'
}

export const NEW_TOKEN = 'baihe_dt_' + 'Q'.repeat(43)
const now = () => Date.now() / 1000

function tokens(): Token[] {
  const t = now()
  return [
    { id: 21, label: 'Home desktop', created_at: t - 9 * 86400, last_used_at: t - 2 * 3600, last_used_ip_prefix: '198.51.100', expires_at: t + 81 * 86400, revoked_at: null, status: 'active' },
    { id: 20, label: 'Old laptop', created_at: t - 40 * 86400, last_used_at: null, last_used_ip_prefix: '', expires_at: null, revoked_at: t - 86400, status: 'revoked' },
  ]
}

interface TokensMock {
  own: Token[]
  all: (Token & { user_id: number; user_name: string })[]
  sent: Request[]
  unmocked: string[]
}

export async function mockExtensionDevices(page: Page, me: MeBody): Promise<TokensMock> {
  const base = await mockDevices(page, me)
  const s: TokensMock = {
    own: tokens(),
    all: tokens().map((t) => ({ ...t, user_id: 9, user_name: 'Jane' })),
    sent: [],
    unmocked: base.unmocked,
  }
  await page.route('**/api/auth/device-tokens**', (route) => {
    const r = route.request()
    const path = new URL(r.url()).pathname
    if (r.method() === 'GET' && path === '/api/auth/device-tokens') {
      return route.fulfill({ json: { tokens: s.own, max_active: 10 }, headers: { 'cache-control': 'no-store' } })
    }
    if (r.method() !== 'POST') return route.abort()
    s.sent.push(r)
    if (path === '/api/auth/device-tokens') {
      const body = r.postDataJSON() as { label: string; expires_in_days: number | null }
      const t = now()
      const row: Token = {
        id: 22, label: body.label, created_at: t, last_used_at: null, last_used_ip_prefix: '',
        expires_at: body.expires_in_days ? t + body.expires_in_days * 86400 : null, revoked_at: null, status: 'active',
      }
      s.own = [row, ...s.own]
      return route.fulfill({ json: { token: NEW_TOKEN, device_token: row } })
    }
    const m = path.match(/^\/api\/auth\/device-tokens\/(\d+)\/revoke$/)
    if (!m) return route.abort()
    s.own = s.own.map((t) => (t.id === Number(m[1]) ? { ...t, status: 'revoked', revoked_at: now() } : t))
    return route.fulfill({ json: { revoked: 1 } })
  })
  await page.route('**/api/admin/device-tokens**', (route) => {
    const r = route.request()
    const path = new URL(r.url()).pathname
    if (r.method() === 'GET' && path === '/api/admin/device-tokens') return route.fulfill({ json: { tokens: s.all } })
    const m = path.match(/^\/api\/admin\/device-tokens\/(\d+)\/revoke$/)
    if (r.method() !== 'POST' || !m) return route.abort()
    s.sent.push(r)
    s.all = s.all.map((t) => (t.id === Number(m[1]) ? { ...t, status: 'revoked', revoked_at: now() } : t))
    return route.fulfill({ json: { revoked: 1 } })
  })
  return s
}
