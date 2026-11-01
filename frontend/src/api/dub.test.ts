import { describe, expect, it } from 'vitest'

import { ApiError } from './client'
import { narrationApi } from './dub'

function fakeFetch(status: number, body: unknown, calls: { url: string; init?: RequestInit }[] = []) {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init })
    return new Response(JSON.stringify(body), { status })
  }) as typeof fetch
}

describe('narration api', () => {
  it('starts narration fresh only when asked, keeping the body', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    const f = fakeFetch(200, { job_id: 'n' }, calls)
    await narrationApi.run(3, { engine: 'claude' }, f, { fresh: true })
    await narrationApi.run(3, { engine: 'claude', model: 'm' }, f, { fresh: false })
    await narrationApi.run(3, { engine: 'claude' }, f)
    expect(calls.map((c) => [c.url, c.init?.method])).toEqual([
      ['/api/narration/dramas/3/run?fresh=true', 'POST'],
      ['/api/narration/dramas/3/run', 'POST'],
      ['/api/narration/dramas/3/run', 'POST'],
    ])
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ engine: 'claude' })
    expect(JSON.parse(String(calls[1].init?.body))).toEqual({ engine: 'claude', model: 'm' })
  })
  it('reads configs and surfaces 409/422/503 as ApiError', async () => {
    expect((await narrationApi.config(2, fakeFetch(200, { is_narration: true }))).is_narration).toBe(true)
    for (const status of [409, 422, 503]) {
      const err = await narrationApi
        .run(1, { engine: 'x' }, fakeFetch(status, { error: { code: 'conflict', message: 'm' } }))
        .catch((e: unknown) => e)
      expect(err).toBeInstanceOf(ApiError)
      expect((err as ApiError).status).toBe(status)
    }
  })
})

