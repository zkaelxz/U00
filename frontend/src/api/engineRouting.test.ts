import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from './client'
import { getEngineRouting, setCapabilityEngine, testEngine } from './engineRouting'
import { getPcMode, resetPcModeForTests } from './pcOnly'

function reply(status: number, body: unknown) {
  const mock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }))
  return { mock, f: mock as unknown as typeof fetch }
}

const localHeader = (init: RequestInit) => new Headers(init.headers).get('X-Baihe-Local')

afterEach(() => resetPcModeForTests())

describe('engine routing api', () => {
  it('GETs the routing', async () => {
    const body = { capabilities: [], engines: [] }
    const { mock, f } = reply(200, body)
    expect(await getEngineRouting(f)).toEqual(body)
    const [url, init] = mock.mock.calls[0]
    expect(url).toBe('/api/settings/engine-routing')
    expect(init.method).toBeUndefined()
  })

  it('sets a task engine with only {engine}, and null for the default', async () => {
    const { mock, f } = reply(200, { id: 'llm.instructions', engine: 'claude' })
    await setCapabilityEngine('llm.instructions', 'claude', f)
    await setCapabilityEngine('llm.instructions', null, f)
    const [url, init] = mock.mock.calls[0]
    expect(url).toBe('/api/settings/engine-routing/capabilities/llm.instructions')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toEqual({ engine: 'claude' })
    expect(localHeader(init)).toBe('1')
    expect(JSON.parse(mock.mock.calls[1][1].body)).toEqual({ engine: null })
  })

  it('tests an engine with an empty body and encodes the name', async () => {
    const { mock, f } = reply(200, { engine: 'test_offline', status: 'working' })
    expect(await testEngine('test_offline', f)).toMatchObject({ status: 'working' })
    const [url, init] = mock.mock.calls[0]
    expect(url).toBe('/api/settings/engine-routing/engines/test_offline/test')
    expect(JSON.parse(init.body)).toEqual({})
    const { mock: m2, f: f2 } = reply(200, {})
    await testEngine('a/b', f2)
    expect(m2.mock.calls[0][0]).toBe('/api/settings/engine-routing/engines/a%2Fb/test')
  })

  it('a 403 marks the tab remote; a 503 is an ApiError', async () => {
    await expect(testEngine('claude', reply(503, { error: { code: 'dependency_unavailable', message: 'No key.' } }).f))
      .rejects.toBeInstanceOf(ApiError)
    expect(getPcMode()).toBe('unknown')
    await expect(setCapabilityEngine('x', 'claude', reply(403, { error: { code: 'forbidden', message: 'PC only.' } }).f))
      .rejects.toMatchObject({ status: 403 })
    expect(getPcMode()).toBe('remote')
  })
})
