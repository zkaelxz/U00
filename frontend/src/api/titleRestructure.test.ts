import { describe, expect, it } from 'vitest'

import { applyRestructure, getRestructure, previewRestructure } from './titleRestructure'

function fakeFetch(calls: { url: string; init?: RequestInit }[]) {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init })
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } })
  }) as typeof fetch
}

describe('title restructure API', () => {
  it('reads the chapter list as a PC-only call', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    await getRestructure(3, fakeFetch(calls))
    expect(calls[0].url).toBe('/api/dramas/3/restructure')
    expect(new Headers(calls[0].init?.headers).get('X-Baihe-Local')).toBe('1')
  })

  it('previews without confirming', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    await previewRestructure(3, { target_id: 9, kind: 'move', chapter_ids: ['src:c1'] }, fakeFetch(calls))
    expect(calls[0].url).toBe('/api/dramas/3/restructure/preview')
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ target_id: 9, kind: 'move', chapter_ids: ['src:c1'] })
  })

  it('applies with the preview fingerprint and confirm', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    await applyRestructure(3, { target_id: 9, kind: 'merge' }, 'abc', fakeFetch(calls))
    expect(calls[0].url).toBe('/api/dramas/3/restructure')
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ target_id: 9, kind: 'merge', fingerprint: 'abc', confirm: true })
  })
})
