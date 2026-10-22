import { describe, expect, it, vi } from 'vitest'

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
      .getDrama(9, fakeFetch(404, { error: { code: 'not_found', message: 'No title with id 9.' } }))
      .catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err.status).toBe(404)
    expect(err.code).toBe('not_found')
    expect(err.message).toBe('No title with id 9.')
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

describe('error mapping inputs', () => {
  it('records the request method on the ApiError', async () => {
    const body = { error: { code: 'validation_error', message: '2000 characters max' } }
    const read = await api.getDrama(1, fakeFetch(422, body)).catch((e) => e)
    expect(read.method).toBe('GET')
    const { postJson } = await import('./client')
    const write = (await postJson('/api/x', {}, fakeFetch(422, body)).catch((e) => e)) as ApiError
    expect([write.status, write.method, write.message]).toEqual([422, 'POST', '2000 characters max'])
  })

  it('a thrown fetch becomes a network_error', async () => {
    const down = (async () => { throw new TypeError('Failed to fetch') }) as typeof fetch
    const err = await api.getDrama(1, down).catch((e) => e)
    expect([err.status, err.code, err.method]).toEqual([0, 'network_error', 'GET'])
  })

  it('a request that never answers ends as a timeout error', async () => {
    vi.useFakeTimers()
    try {
      const hung = ((_: RequestInfo | URL, init?: RequestInit) =>
        new Promise((_res, rej) => {
          init?.signal?.addEventListener('abort', () => rej(new DOMException('aborted', 'AbortError')))
        })) as typeof fetch
      const { postJson } = await import('./client')
      const pending = postJson('/api/x', {}, hung).catch((e) => e as ApiError)
      await vi.advanceTimersByTimeAsync(120_000)
      const err = (await pending) as ApiError
      expect([err.status, err.code, err.method]).toEqual([0, 'timeout', 'POST'])
    } finally {
      vi.useRealTimers()
    }
  })

  it('an upload still sending after the timeout is not cut off', async () => {
    vi.useFakeTimers()
    try {
      let aborted = false
      const slow = ((_: RequestInfo | URL, init?: RequestInit) =>
        new Promise((res) => {
          init?.signal?.addEventListener('abort', () => (aborted = true))
          setTimeout(() => res(new Response('{}', { status: 200 })), 600_000)
        })) as typeof fetch
      const { postMultipart } = await import('./client')
      const pending = postMultipart('/api/up', new FormData(), slow)
      await vi.advanceTimersByTimeAsync(600_000)
      expect(await pending).toEqual({})
      expect(aborted).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })
})
