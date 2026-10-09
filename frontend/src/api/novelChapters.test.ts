import { describe, expect, it } from 'vitest'

import { getRawChapterText, getRawChapters } from './novelChapters'

function fakeFetch(urls: string[]) {
  return (async (input: RequestInfo | URL) => {
    urls.push(String(input))
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } })
  }) as typeof fetch
}

describe('novel chapters API', () => {
  it('asks for one bounded page of rows', async () => {
    const urls: string[] = []
    await getRawChapters(4, 100, 50, fakeFetch(urls))
    expect(urls).toEqual(['/api/novel/dramas/4/raw-novel/chapters?offset=100&limit=50'])
  })
  it('asks for one bounded slice of a chapter', async () => {
    const urls: string[] = []
    await getRawChapterText(4, 7, 20000, 50000, fakeFetch(urls))
    expect(urls).toEqual(['/api/novel/dramas/4/raw-novel/chapters/7?offset=20000&limit=50000'])
  })
})
