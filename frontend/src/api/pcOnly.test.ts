import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from './client'
import {
  applyMeta, getPcMetaFailed, getPcMode, loadPcMode, markRemote, pcOnlyFetch, resetPcModeForTests, subscribePcMode,
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
    const heard = vi.fn()
    subscribePcMode(heard)
    expect(getPcMetaFailed()).toBe(false)
    await loadPcMode(() => Promise.reject(new ApiError(0, { code: 'network_error', message: '' })))
    expect(getPcMode()).toBe('unknown')
    expect(getPcMetaFailed()).toBe(true)
    expect(heard).toHaveBeenCalled()
  })

  it('a meta answer without a boolean local is unconfirmed, not checking forever', async () => {
    await loadPcMode(() => Promise.resolve(meta(undefined)))
    expect(getPcMode()).toBe('unknown')
    expect(getPcMetaFailed()).toBe(true)
  })

  it('still unknown when applyMeta gets no local field', () => {
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
