import { afterEach, describe, expect, it, vi } from 'vitest'

import { accountWords, loginErrorMessage, nameWidth, loginHref, logout, me, readLoginError, safeReturnTo } from './auth'

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
    for (const bad of ['', 'https://evil.example', '//evil.example', '/\\evil.example', 'evil', null, undefined,
      '/\t/evil.example', '/\n/evil.example', '/\r/evil.example', '/ /evil.example', '/\u0000/x']) {
      expect(safeReturnTo(bad)).toBe('/')
    }
    expect(safeReturnTo('/#/read/3')).toBe('/#/read/3')
    expect(safeReturnTo('/?q=a%20b#/drama/2')).toBe('/?q=a%20b#/drama/2')
    expect(loginHref('/\t/evil.example')).toBe('/api/auth/login?return_to=%2F')
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
    expect(loginErrorMessage('not_allowed')).toMatch(/test users in Google Cloud/)
    expect(loginErrorMessage('expired')).toMatch(/expired/)
    expect(loginErrorMessage('provider_error')).toMatch(/Google sign-in didn't work/)
    const generic = "Sign-in didn't work. Please try again."
    expect(loginErrorMessage('toString')).toBe(generic)
    expect(loginErrorMessage('<script>')).toBe(generic)
  })
})

describe('accountWords', () => {
  it('shows a name of up to 24 characters whole and anything longer, blank or missing as Account', () => {
    const name24 = 'A'.repeat(24)
    expect(accountWords(name24)).toBe(name24)
    expect(accountWords('A'.repeat(25))).toBe('Account')
    expect(accountWords('  Alex  ')).toBe('Alex')
    expect(accountWords('   ')).toBe('Account')
    expect(accountWords('')).toBe('Account')
    expect(accountWords(null)).toBe('Account')
    expect(accountWords(undefined)).toBe('Account')
  })

  it('counts wide characters as two: 12 kanji fit, 13 give Account', () => {
    expect(accountWords('白'.repeat(12))).toBe('白'.repeat(12))
    expect(accountWords('白'.repeat(13))).toBe('Account')
    expect(accountWords('𠮷'.repeat(12))).toBe('𠮷'.repeat(12))
    expect(accountWords('Ab' + 'あ'.repeat(11))).toBe('Ab' + 'あ'.repeat(11))
    expect(accountWords('Abc' + 'あ'.repeat(11))).toBe('Account')
    // The long mark and the middle dot are as wide as a kana.
    expect(accountWords('ア'.repeat(12) + 'ー')).toBe('Account')
    expect(accountWords('Abc' + 'あ'.repeat(10) + 'ー')).toBe('Account')
    expect(accountWords('Ab' + 'あ'.repeat(10) + 'ー')).toBe('Ab' + 'あ'.repeat(10) + 'ー')
  })
})

describe('nameWidth', () => {
  it('is one for Latin letters, two for kanji, kana, Hangul, fullwidth forms and emoji, none for joiners', () => {
    expect(nameWidth('Alex')).toBe(3)
    expect(nameWidth('白'.repeat(12))).toBe(24)
    expect(nameWidth('かな')).toBe(4)
    expect(nameWidth('한글')).toBe(4)
    expect(nameWidth('ＡＢ')).toBe(4)
    expect(nameWidth('😀')).toBe(2)
    expect(nameWidth('👩‍💻')).toBe(4)
    expect(nameWidth('ジョージ・ワシントン')).toBe(20)
    expect(nameWidth('')).toBe(0)
  })
})
