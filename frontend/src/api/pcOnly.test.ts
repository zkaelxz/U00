import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from './client'
import {
  applyMeta, getPcMode, loadPcMode, markRemote, pcOnlyFetch, resetPcModeForTests, subscribePcMode,
} from './pcOnly'
import type { MetaResponse } from './types'

const meta = (local?: boolean): MetaResponse => ({ app: 'Baihe Studio', api_version: '0.1', environment: 'x', local })

afterEach(() => resetPcModeForTests())

describe('PC-only mode', () => {
  it('starts unknown and follows /api/meta', async () => {
    expect(getPcMode()).toBe('unknown')
    await loadPcMode(() => Promise.resolve(meta(true)))
    expect(getPcMode()).toBe('local')
    resetPcModeForTests()
    await loadPcMode(() => Promise.resolve(meta(false)))
    expect(getPcMode()).toBe('remote')
  })

  it('stays unknown when meta fails or has no local field (older API)', async () => {
    await loadPcMode(() => Promise.reject(new ApiError(0, { code: 'network_error', message: '' })))
    expect(getPcMode()).toBe('unknown')
    applyMeta(meta(undefined))
    expect(getPcMode()).toBe('unknown')
  })

  it('fetches meta only once per page load', async () => {
    const m = vi.fn().mockResolvedValue(meta(true))
    await Promise.all([loadPcMode(m), loadPcMode(m)])
    expect(m).toHaveBeenCalledTimes(1)
  })

  it('a 403 this page load wins over meta local: true', async () => {
    markRemote()
    await loadPcMode(() => Promise.resolve(meta(true)))
    expect(getPcMode()).toBe('remote')
  })

  it('meta local: true on a later page load clears a remembered 403', () => {
    const store = new Map<string, string>([['baihe.pcOnly', 'remote']])
    vi.stubGlobal('window', { sessionStorage: {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => void store.set(k, v),
      removeItem: (k: string) => void store.delete(k),
    } })
    try {
      resetPcModeForTests('remote') // a new page load that remembered remote
      applyMeta(meta(true))
      expect(getPcMode()).toBe('local')
      expect(store.has('baihe.pcOnly')).toBe(false)
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('markRemote notifies subscribers', () => {
    const l = vi.fn()
    subscribePcMode(l)
    markRemote()
    expect(getPcMode()).toBe('remote')
    expect(l).toHaveBeenCalledTimes(1)
  })

  it('pcOnlyFetch adds the header, keeps others, and a 403 marks remote', async () => {
    const f = vi.fn().mockResolvedValue(new Response('{}', { status: 200 }))
    await pcOnlyFetch(f as unknown as typeof fetch)('/x', { headers: { Accept: 'application/json' } })
    const h = new Headers(f.mock.calls[0][1].headers)
    expect(h.get('X-Baihe-Local')).toBe('1')
    expect(h.get('Accept')).toBe('application/json')
    expect(getPcMode()).toBe('unknown')
    const g = vi.fn().mockResolvedValue(new Response('{}', { status: 403 }))
    await pcOnlyFetch(g as unknown as typeof fetch)('/x')
    expect(getPcMode()).toBe('remote')
  })
})

describe('pcOnlyFetch and CSRF', () => {
  const reply = (status: number, code: string) =>
    (async () => new Response(JSON.stringify({ error: { code, message: 'x' } }), { status })) as typeof fetch

  it('a csrf_failed 403 does not flip PC mode to remote', async () => {
    await loadPcMode(() => Promise.resolve(meta(true)))
    const resp = await pcOnlyFetch(reply(403, 'csrf_failed'))('/api/x', { method: 'POST' })
    expect(getPcMode()).toBe('local')
    // The caller can still read the body.
    expect(((await resp.json()) as { error: { code: string } }).error.code).toBe('csrf_failed')
  })

  it('any other 403 still flips to remote', async () => {
    await loadPcMode(() => Promise.resolve(meta(true)))
    await pcOnlyFetch(reply(403, 'local_only'))('/api/x', { method: 'POST' })
    expect(getPcMode()).toBe('remote')
  })

  it('a 403 with a non-JSON body flips to remote', async () => {
    await pcOnlyFetch((async () => new Response('<html>', { status: 403 })) as typeof fetch)('/api/x', { method: 'POST' })
    expect(getPcMode()).toBe('remote')
  })

  it('adds X-CSRF-Token on mutations when the cookie is set, not on GET', async () => {
    vi.stubGlobal('document', { cookie: 'baihe_csrf=tok' })
    try {
      const seen: Headers[] = []
      const f = (async (_i: RequestInfo | URL, init?: RequestInit) => {
        seen.push(new Headers(init?.headers))
        return new Response('{}', { status: 200 })
      }) as typeof fetch
      await pcOnlyFetch(f)('/api/x', { method: 'DELETE' })
      await pcOnlyFetch(f)('/api/x')
      expect(seen[0].get('X-CSRF-Token')).toBe('tok')
      expect(seen[0].get('X-Baihe-Local')).toBe('1')
      expect(seen[1].get('X-CSRF-Token')).toBeNull()
    } finally {
      vi.unstubAllGlobals()
    }
  })
})
