import { describe, expect, it } from 'vitest'

import { getLncrawlStatus, lncrawlJobId, startLncrawlImport } from '../../../api/workspace'
import { LNCRAWL_MAX_CHAPTERS, lncrawlNotice, lncrawlRequest } from './lncrawlForm'

const URL = 'https://novels.example.com/book/1'

describe('lncrawlRequest', () => {
  it('builds the body for all chapters without a count', () => {
    expect(lncrawlRequest(` ${URL} `, 'all', '12', 'append')).toEqual({ body: { url: URL, chapters: 'all', mode: 'append' } })
  })

  it('needs a whole count from 1 for first/last', () => {
    expect(lncrawlRequest(URL, 'first', '10', 'replace')).toEqual({ body: { url: URL, chapters: 'first', count: 10, mode: 'replace' } })
    for (const bad of ['', '0', '2.5', 'x', String(LNCRAWL_MAX_CHAPTERS + 1)]) {
      expect(lncrawlRequest(URL, 'last', bad, 'replace')).toHaveProperty('problem')
    }
  })

  it('refuses addresses that are not http(s), or carry spaces/control characters', () => {
    for (const bad of ['', 'ftp://x.com/a', 'file:///etc/passwd', 'javascript:alert(1)', '--config=x',
      'https://a b.com/', 'https://a.com/\u0007', 'https://']) {
      expect(lncrawlRequest(bad, 'all', '', 'replace')).toHaveProperty('problem')
    }
  })
})

describe('lncrawlNotice', () => {
  it('reads the job result', () => {
    expect(lncrawlNotice({ char_count: 12345, epub_chapters: 40 })).toBe(`Imported ${(12345).toLocaleString()} characters (40 EPUB sections).`)
    expect(lncrawlNotice({ char_count: 5 })).toBe('Imported 5 characters.')
    expect(lncrawlNotice(null)).toBe('Imported.')
  })
})

describe('lncrawl api', () => {
  it('reads the status and posts the import as a PC-only JSON call', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    const f = (async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), init })
      const body = String(input).endsWith('/lncrawl') && !init?.method ? { installed: true, path_configured: false } : { job_id: 'lncrawl_4' }
      return new Response(JSON.stringify(body), { status: 200 })
    }) as typeof fetch
    expect(await getLncrawlStatus(f)).toEqual({ installed: true, path_configured: false })
    expect(calls[0].url).toBe('/api/novel/lncrawl')
    const r = await startLncrawlImport(4, { url: URL, chapters: 'first', count: 3, mode: 'append' }, f)
    expect(r.job_id).toBe(lncrawlJobId(4))
    expect(calls[1].url).toBe('/api/novel/dramas/4/lncrawl')
    expect(calls[1].init?.method).toBe('POST')
    expect(JSON.parse(String(calls[1].init?.body))).toEqual({ url: URL, chapters: 'first', count: 3, mode: 'append' })
    expect(new Headers(calls[1].init?.headers).get('X-Baihe-Local')).toBe('1')
  })
})
