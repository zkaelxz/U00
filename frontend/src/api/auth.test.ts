import { afterEach, describe, expect, it, vi } from 'vitest'

import { loginErrorMessage, loginHref, logout, me, readLoginError, safeReturnTo } from './auth'

interface Call {
  url: string
  init?: RequestInit
}

function fakeFetch(status: number, body: unknown, calls: Call[]): typeof fetch {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init })
    return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  }) as typeof fetch
}

afterEach(() => vi.unstubAllGlobals())

describe('auth API wrappers', () => {
  it('me() GETs /api/auth/me with no CSRF header', async () => {
    vi.stubGlobal('document', { cookie: 'baihe_csrf=tok' })
    const calls: Call[] = []
    const body = { auth_enabled: true, signed_in: false, sign_in_configured: true, zone: null, user: null, permissions: [] }
    await expect(me(fakeFetch(200, body, calls))).resolves.toEqual(body)
    expect(calls[0].url).toBe('/api/auth/me')
    expect(calls[0].init?.method).toBeUndefined()
    const h = (calls[0].init?.headers ?? {}) as Record<string, string>
    expect(h['X-CSRF-Token']).toBeUndefined()
  })

  it('logout() POSTs with the CSRF and local headers', async () => {
    vi.stubGlobal('document', { cookie: '__Host-baihe_csrf=abc%2B1' })
    const calls: Call[] = []
    await logout(fakeFetch(200, { signed_out: true }, calls))
    expect(calls[0].url).toBe('/api/auth/logout')
    expect(calls[0].init?.method).toBe('POST')
    const h = calls[0].init?.headers as Record<string, string>
    expect(h['X-CSRF-Token']).toBe('abc+1')
    expect(h['X-Baihe-Local']).toBe('1')
  })
})

describe('login link', () => {
  it('encodes a relative return path', () => {
    expect(loginHref('/#/settings')).toBe('/api/auth/login?return_to=%2F%23%2Fsettings')
    expect(loginHref('/')).toBe('/api/auth/login?return_to=%2F')
  })

  it('refuses anything that is not a same-site path', () => {
    for (const bad of ['', 'https://evil.example', '//evil.example', '/\\evil.example', 'evil', null, undefined]) {
      expect(safeReturnTo(bad)).toBe('/')
    }
    expect(safeReturnTo('/#/read/3')).toBe('/#/read/3')
  })
})

describe('login_error', () => {
  it('reads the code from the query string', () => {
    expect(readLoginError('?login_error=denied')).toBe('denied')
    expect(readLoginError('?x=1&login_error=expired')).toBe('expired')
    expect(readLoginError('')).toBeNull()
    expect(readLoginError('?login_error=')).toBeNull()
  })

  it('has a plain message for each code, and a generic one otherwise', () => {
    expect(loginErrorMessage('denied')).toBe('Sign-in was cancelled. You can try again.')
    expect(loginErrorMessage('not_allowed')).toMatch(/isn't on this household's list/)
    expect(loginErrorMessage('expired')).toMatch(/expired/)
    expect(loginErrorMessage('provider_error')).toMatch(/Google sign-in didn't work/)
    const generic = "Sign-in didn't work. Please try again."
    expect(loginErrorMessage('toString')).toBe(generic)
    expect(loginErrorMessage('<script>')).toBe(generic)
  })
})
