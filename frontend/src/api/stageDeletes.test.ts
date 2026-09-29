import { describe, expect, it } from 'vitest'

import {
  deleteSeriesCharacter,
  deleteVersion,
  listSeriesCharacters,
  removeMedia,
  removeRawNovel,
  withLocalHeader,
} from './stageDeletes'

type Call = { url: string; init?: RequestInit }

function fakeFetch(calls: Call[]) {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init })
    return new Response('{}', { status: 200 })
  }) as typeof fetch
}

describe('PC-only stage deletes', () => {
  it('post {confirm:true} to the delete routes with X-Baihe-Local: 1', async () => {
    const calls: Call[] = []
    const f = fakeFetch(calls)
    await removeMedia(3, f)
    await removeRawNovel(3, f)
    await deleteVersion(3, 9, f)
    await deleteSeriesCharacter(5, 11, f)
    expect(calls.map((c) => c.url)).toEqual([
      '/api/media/dramas/3/remove',
      '/api/novel/dramas/3/raw-novel/remove',
      '/api/review/dramas/3/versions/9/delete',
      '/api/characters/series/5/characters/11/delete',
    ])
    for (const c of calls) {
      expect(c.init?.method).toBe('POST')
      expect(JSON.parse(String(c.init?.body))).toEqual({ confirm: true })
      const h = new Headers(c.init?.headers)
      expect(h.get('X-Baihe-Local')).toBe('1')
      expect(h.get('Content-Type')).toBe('application/json')
    }
  })

  it('reads the series cast without the local header', async () => {
    const calls: Call[] = []
    await listSeriesCharacters(5, fakeFetch(calls))
    expect(calls[0].url).toBe('/api/characters/series/5/characters')
    expect(new Headers(calls[0].init?.headers).get('X-Baihe-Local')).toBeNull()
  })

  it('withLocalHeader keeps existing headers', async () => {
    const calls: Call[] = []
    await withLocalHeader(fakeFetch(calls))('/x', { headers: { Accept: 'application/json' } })
    const h = new Headers(calls[0].init?.headers)
    expect(h.get('Accept')).toBe('application/json')
    expect(h.get('X-Baihe-Local')).toBe('1')
  })
})
