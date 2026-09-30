import { afterEach, describe, expect, it, vi } from 'vitest'

import { resetPcModeForTests } from './pcOnly'
import { getWebSearchConfig, getWebSearchStatus, saveWebSearchConfig, searchWeb, testWebSearch } from './webSearch'

function reply(status: number, body: unknown) {
  const mock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }))
  return { mock, f: mock as unknown as typeof fetch }
}

const localHeader = (init: RequestInit | undefined) => new Headers(init?.headers).get('X-Baihe-Local')

afterEach(() => resetPcModeForTests())

describe('web search api', () => {
  it('status and search are plain library calls; search sends only the query', async () => {
    const s = reply(200, { enabled: true })
    expect(await getWebSearchStatus(s.f)).toEqual({ enabled: true })
    expect(s.mock.mock.calls[0][0]).toBe('/api/web-search/status')
    expect(localHeader(s.mock.mock.calls[0][1])).toBeNull()

    const q = reply(200, { query: 'x', source: 'searxng', results: [] })
    await searchWeb('x', q.f)
    const [url, init] = q.mock.mock.calls[0]
    expect(url).toBe('/api/web-search/search')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toEqual({ query: 'x' })
  })

  it('config and test are PC only; an address change uses the plain fetch', async () => {
    const g = reply(200, { enabled: false, base_url: null })
    await getWebSearchConfig(g.f)
    expect(localHeader(g.mock.mock.calls[0][1])).toBe('1')

    const t = reply(200, { enabled: true, base_url: null })
    await saveWebSearchConfig({ enabled: true }, t.f)
    expect(localHeader(t.mock.mock.calls[0][1])).toBe('1')

    const a = reply(200, { enabled: false, base_url: 'http://localhost:8888' })
    await saveWebSearchConfig({ base_url: 'http://localhost:8888', confirm: true }, a.f)
    const [url, init] = a.mock.mock.calls[0]
    expect(url).toBe('/api/web-search/config')
    expect(JSON.parse(init.body)).toEqual({ base_url: 'http://localhost:8888', confirm: true })

    const x = reply(200, { ok: true, result_count: 3 })
    expect(await testWebSearch(x.f)).toEqual({ ok: true, result_count: 3 })
    expect(x.mock.mock.calls[0][0]).toBe('/api/web-search/test')
    expect(localHeader(x.mock.mock.calls[0][1])).toBe('1')
  })
})
