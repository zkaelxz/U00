import { describe, expect, it } from 'vitest'

import { updateDramaMetadata } from './library'
import { getSourceConfig, updateSourceConfig } from './source'

function fakeFetch(calls: { url: string; init?: RequestInit }[]) {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init })
    return new Response('{}', { status: 200 })
  }) as typeof fetch
}

describe('source config + metadata api', () => {
  it('uses the existing routes and sends only the given keys', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    const f = fakeFetch(calls)
    await getSourceConfig(7, f)
    await updateSourceConfig(7, { content_mode: 'streamer_vod' }, f)
    await updateDramaMetadata(7, { studio: 'S' }, f)
    expect(calls.map((c) => [c.url, c.init?.method ?? 'GET'])).toEqual([
      ['/api/source/dramas/7/config', 'GET'],
      ['/api/source/dramas/7/config', 'POST'],
      ['/api/dramas/7/metadata', 'POST'],
    ])
    expect(JSON.parse(String(calls[1].init?.body))).toEqual({ content_mode: 'streamer_vod' })
    expect(JSON.parse(String(calls[2].init?.body))).toEqual({ studio: 'S' })
  })
})
