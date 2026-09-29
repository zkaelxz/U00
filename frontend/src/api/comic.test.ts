import { describe, expect, it } from 'vitest'

import { ApiError } from './client'
import { comicApi, comicImageUrl, probeImage } from './comic'

type Call = { url: string; init?: RequestInit }

function fakeFetch(status: number, body: unknown, calls: Call[]) {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init })
    return new Response(body === null ? null : JSON.stringify(body), { status })
  }) as typeof fetch
}

describe('comic api', () => {
  it('reads pages, regions and progress from the scanlate routes', async () => {
    const calls: Call[] = []
    const f = fakeFetch(200, {}, calls)
    await comicApi.pages(4, f)
    await comicApi.regions(4, 17, f)
    await comicApi.progress(4, f)
    expect(calls.map((c) => c.url)).toEqual([
      '/api/scanlate/dramas/4/pages',
      '/api/scanlate/dramas/4/pages/17/regions',
      '/api/scanlate/dramas/4/progress',
    ])
    expect(calls.every((c) => !c.init?.method)).toBe(true)
  })

  it('saves progress with only the page, as JSON with the local header', async () => {
    const calls: Call[] = []
    await comicApi.saveProgress(4, 12, fakeFetch(200, { last_page: 12, percent_complete: 40 }, calls))
    expect(calls[0].url).toBe('/api/scanlate/dramas/4/progress')
    expect(calls[0].init?.method).toBe('POST')
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ page: 12 })
    expect(new Headers(calls[0].init!.headers).get('X-Baihe-Local')).toBe('1')
  })

  it('turns a refusal into an ApiError with the server code', async () => {
    const f = fakeFetch(422, { error: { code: 'invalid_input', message: 'Page is past the end.' } }, [])
    const err = await comicApi.saveProgress(4, 99, f).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(422)
    expect((err as ApiError).code).toBe('invalid_input')
  })

  it('builds image URLs with the variant and an optional cache-buster', () => {
    expect(comicImageUrl(4, 17)).toBe('/api/scanlate/dramas/4/pages/17/image?variant=original')
    expect(comicImageUrl(4, 17, 'rendered', 1712)).toBe('/api/scanlate/dramas/4/pages/17/image?variant=rendered&v=1712')
    expect(comicImageUrl(4, 17, 'original', '')).toBe('/api/scanlate/dramas/4/pages/17/image?variant=original')
  })

  it('probes a failed image with HEAD and names the reason', async () => {
    const calls: Call[] = []
    expect(await probeImage('/x', fakeFetch(403, null, calls))).toBe('forbidden')
    expect(calls[0].init?.method).toBe('HEAD')
    expect(await probeImage('/x', fakeFetch(401, null, []))).toBe('forbidden')
    expect(await probeImage('/x', fakeFetch(404, null, []))).toBe('missing')
    expect(await probeImage('/x', fakeFetch(500, null, []))).toBe('failed')
    const down = (async () => {
      throw new TypeError('offline')
    }) as unknown as typeof fetch
    expect(await probeImage('/x', down)).toBe('failed')
  })
})
