import { describe, expect, it, vi } from 'vitest'

import { ApiError } from './client'
import { getStrongerSuggestions, tryStrongerEngine } from './strongerEngine'

function reply(status: number, body: unknown) {
  const mock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }))
  return { mock, f: mock as unknown as typeof fetch }
}

describe('stronger engine api', () => {
  it('GETs the suggestions for a drama', async () => {
    const body = { drama_id: 3, engine: 'claude', current_engine: 'deepseek', available: true, reason_labels: {}, lines: [] }
    const { mock, f } = reply(200, body)
    expect(await getStrongerSuggestions(3, f)).toEqual(body)
    const [url, init] = mock.mock.calls[0]
    expect(url).toBe('/api/stronger-engine/dramas/3')
    expect(init.method).toBeUndefined()
  })

  it('tries one line with an empty body', async () => {
    const out = { drama_id: 3, line_id: 7, engine: 'claude', model: 'm', text: 'Hi', based_on_en: 'Hello', cost_usd: 0.001 }
    const { mock, f } = reply(200, out)
    expect(await tryStrongerEngine(3, 7, f)).toEqual(out)
    const [url, init] = mock.mock.calls[0]
    expect(url).toBe('/api/stronger-engine/dramas/3/lines/7/try')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toEqual({})
  })

  it('a refusal is an ApiError carrying the status and server message', async () => {
    const { f } = reply(400, { error: { code: 'unsupported_operation', message: 'Over the cap.' } })
    const err = await tryStrongerEngine(3, 7, f).catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).toMatchObject({ status: 400, message: 'Over the cap.' })
  })
})
