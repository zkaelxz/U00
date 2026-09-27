import { describe, expect, it } from 'vitest'

import { api, ApiError, buildDramaQuery } from './client'

function fakeFetch(status: number, body: unknown, calls: string[] = []): typeof fetch {
  return (async (input: RequestInfo | URL) => {
    calls.push(String(input))
    return new Response(typeof body === 'string' ? body : JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    })
  }) as typeof fetch
}

describe('buildDramaQuery', () => {
  it('omits empty filters and repeats tags', () => {
    expect(buildDramaQuery({ search: 'moon', status: '', tag: ['bl', 'wuxia'] })).toBe(
      '?search=moon&tag=bl&tag=wuxia',
    )
    expect(buildDramaQuery({})).toBe('')
  })
})

describe('api client', () => {
  it('returns the parsed body on success', async () => {
    const calls: string[] = []
    const body = { items: [], count: 0 }
    await expect(api.listDramas({ search: 'x' }, fakeFetch(200, body, calls))).resolves.toEqual(body)
    expect(calls).toEqual(['/api/library/dramas?search=x'])
  })

  it('turns the API error shape into an ApiError', async () => {
    const err = await api
      .getDrama(9, fakeFetch(404, { error: { code: 'not_found', message: 'No drama with id 9.' } }))
      .catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err.status).toBe(404)
    expect(err.code).toBe('not_found')
    expect(err.message).toBe('No drama with id 9.')
  })

  it('copes with a non-JSON error body', async () => {
    const err = await api.health(fakeFetch(502, '<html>Bad Gateway</html>')).catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err.status).toBe(502)
    expect(err.code).toBe('internal_error')
  })

  it('reports an unreachable server as a network error', async () => {
    const down = (async () => {
      throw new TypeError('fetch failed')
    }) as typeof fetch
    const err = await api.health(down).catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err.code).toBe('network_error')
  })
})
