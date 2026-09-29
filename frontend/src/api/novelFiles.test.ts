import { afterEach, describe, expect, it } from 'vitest'

import {
  getNovelReference,
  getRawNovel,
  removeNovelReference,
  saveNovelReferenceText,
  saveRawNovelText,
  uploadNovelReference,
  uploadRawNovel,
} from './novelFiles'
import { getPcMode, resetPcModeForTests } from './pcOnly'

type Call = { url: string; init?: RequestInit }

function fakeFetch(calls: Call[], status = 200, body: unknown = {}) {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init })
    return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  }) as typeof fetch
}

afterEach(() => resetPcModeForTests())

describe('novel file API', () => {
  it('reads status without the local header', async () => {
    const calls: Call[] = []
    const f = fakeFetch(calls, 200, { drama_id: 4, present: false, size_bytes: 0, char_count: 0 })
    await getNovelReference(4, f)
    await getRawNovel(4, f)
    expect(calls.map((c) => c.url)).toEqual(['/api/novel/dramas/4/reference', '/api/novel/dramas/4/raw-novel'])
    for (const c of calls) expect(new Headers(c.init?.headers).get('X-Baihe-Local')).toBeNull()
  })

  it('uploads multipart with X-Baihe-Local and no explicit Content-Type', async () => {
    const calls: Call[] = []
    const f = fakeFetch(calls)
    const file = new File(['hello'], 'book.txt', { type: 'text/plain' })
    await uploadNovelReference(4, file, f)
    await uploadRawNovel(4, file, f)
    expect(calls.map((c) => c.url)).toEqual(['/api/novel/dramas/4/reference', '/api/novel/dramas/4/raw-novel'])
    for (const c of calls) {
      expect(c.init?.method).toBe('POST')
      const h = new Headers(c.init?.headers)
      expect(h.get('X-Baihe-Local')).toBe('1')
      expect(h.get('Content-Type')).toBeNull()
      const form = c.init?.body as FormData
      expect((form.get('file') as File).name).toBe('book.txt')
    }
  })

  it('removes the reference with {confirm: true}', async () => {
    const calls: Call[] = []
    await removeNovelReference(4, fakeFetch(calls))
    expect(calls[0].url).toBe('/api/novel/dramas/4/reference/remove')
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ confirm: true })
    expect(new Headers(calls[0].init?.headers).get('X-Baihe-Local')).toBe('1')
  })

  it('saves pasted text as JSON {text} with X-Baihe-Local', async () => {
    const calls: Call[] = []
    const f = fakeFetch(calls)
    await saveNovelReferenceText(4, 'Chapter 1', f)
    await saveRawNovelText(4, '第一章', f)
    expect(calls.map((c) => c.url)).toEqual([
      '/api/novel/dramas/4/reference/text',
      '/api/novel/dramas/4/raw-novel/text',
    ])
    expect(calls.map((c) => JSON.parse(String(c.init?.body)))).toEqual([{ text: 'Chapter 1' }, { text: '第一章' }])
    for (const c of calls) {
      const h = new Headers(c.init?.headers)
      expect(c.init?.method).toBe('POST')
      expect(h.get('X-Baihe-Local')).toBe('1')
      expect(h.get('Content-Type')).toBe('application/json')
    }
  })

  it('a 403 on upload marks the tab remote', async () => {
    const f = fakeFetch([], 403, { error: { code: 'forbidden', message: 'Not allowed.' } })
    await expect(uploadRawNovel(4, new File(['x'], 'a.txt'), f)).rejects.toBeTruthy()
    expect(getPcMode()).toBe('remote')
  })
})
