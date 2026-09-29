import { describe, expect, it } from 'vitest'

import { analyzeMedia, applyMetadata, suggestMetadata } from './metadata'

function fakeFetch(calls: { url: string; init?: RequestInit }[]) {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init })
    return new Response('{}', { status: 200 })
  }) as typeof fetch
}

describe('metadata api', () => {
  it('posts to the Slice 37 routes', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    const f = fakeFetch(calls)
    await analyzeMedia(4, f)
    await suggestMetadata(4, { url: 'https://x.test' }, f)
    await applyMetadata(4, { studio: 'S' }, f)
    expect(calls.map((c) => c.url)).toEqual([
      '/api/metadata/dramas/4/analyze-media',
      '/api/metadata/dramas/4/autofill',
      '/api/metadata/dramas/4/autofill/apply',
    ])
    expect(JSON.parse(String(calls[1].init?.body))).toEqual({ url: 'https://x.test' })
    expect(JSON.parse(String(calls[2].init?.body))).toEqual({ studio: 'S' })
  })
})
