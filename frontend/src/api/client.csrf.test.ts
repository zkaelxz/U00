import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, deleteJson, getJson, onUnauthorized, postJson, postMultipart, readCsrfToken } from './client'

interface Call {
  url: string
  init?: RequestInit
}

function fakeFetch(status: number, body: unknown, calls: Call[] = []): typeof fetch {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init })
    return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  }) as typeof fetch
}

const header = (c: Call, name: string) => (c.init?.headers as Record<string, string> | undefined)?.[name]

afterEach(() => vi.unstubAllGlobals())

describe('readCsrfToken', () => {
  it('prefers the __Host- cookie, falls back to the dev name, decodes', () => {
    expect(readCsrfToken('a=1; __Host-baihe_csrf=sec; baihe_csrf=dev')).toBe('sec')
    expect(readCsrfToken('a=1; baihe_csrf=dev%3D%3D')).toBe('dev==')
    expect(readCsrfToken('baihe_csrf_other=x; xbaihe_csrf=y')).toBeNull()
    expect(readCsrfToken('')).toBeNull()
  })

  it('reads document.cookie by default and copes with no document', () => {
    expect(readCsrfToken()).toBeNull()
    vi.stubGlobal('document', { cookie: 'baihe_csrf=t1' })
    expect(readCsrfToken()).toBe('t1')
  })
})

describe('CSRF header on mutations', () => {
  it('POST, DELETE and multipart carry X-CSRF-Token and X-Baihe-Local; GET carries neither', async () => {
    vi.stubGlobal('document', { cookie: 'baihe_csrf=tok' })
    const calls: Call[] = []
    const f = fakeFetch(200, {}, calls)
    await postJson('/api/a', { x: 1 }, f)
    await postJson('/api/b', undefined, f)
    await deleteJson('/api/c', f)
    await postMultipart('/api/d', new FormData(), f)
    await getJson('/api/e', f)
    for (const c of calls.slice(0, 4)) {
      expect(header(c, 'X-CSRF-Token'), c.url).toBe('tok')
      expect(header(c, 'X-Baihe-Local'), c.url).toBe('1')
    }
    expect(header(calls[0], 'Content-Type')).toBe('application/json')
    expect(header(calls[3], 'Content-Type')).toBeUndefined()
    expect(header(calls[4], 'X-CSRF-Token')).toBeUndefined()
    expect(header(calls[4], 'X-Baihe-Local')).toBeUndefined()
  })

  it('no cookie (auth off): no CSRF header, requests unchanged', async () => {
    vi.stubGlobal('document', { cookie: '' })
    const calls: Call[] = []
    await postJson('/api/a', { x: 1 }, fakeFetch(200, {}, calls))
    expect(header(calls[0], 'X-CSRF-Token')).toBeUndefined()
    expect(header(calls[0], 'X-Baihe-Local')).toBe('1')
  })
})

describe('401', () => {
  it('notifies listeners and still throws the ApiError; a 403 does not', async () => {
    const seen = vi.fn()
    const off = onUnauthorized(seen)
    try {
      const err = await getJson('/api/x', fakeFetch(401, { error: { code: 'unauthorized', message: 'Sign in first.' } })).catch((e) => e)
      expect(err).toBeInstanceOf(ApiError)
      expect((err as ApiError).status).toBe(401)
      expect(seen).toHaveBeenCalledTimes(1)
      await getJson('/api/x', fakeFetch(403, { error: { code: 'forbidden', message: 'No.' } })).catch(() => null)
      expect(seen).toHaveBeenCalledTimes(1)
    } finally {
      off()
    }
  })
})
