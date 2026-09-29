import { describe, expect, it } from 'vitest'

import type { BulkEntry, KnownTitle } from '../../types/discover'
import {
  EMPTY_TITLE,
  MAX_PAGINATE_PAGES,
  applySuggestion,
  bulkUrlsProblem,
  catalogCount,
  commitEntries,
  discoverEngines,
  existingDramaId,
  hasChinese,
  mediaLabel,
  paginateUrls,
  parseUrlList,
  safeHref,
  sourceLabel,
  titleFormProblems,
  titleMeta,
} from './discoverFormat'

const eng = (name: string, key_configured = true) => ({ name, label: name, free: false, models: null, key_configured })

describe('discoverEngines', () => {
  it('keeps configured engines the Discover routes accept', () => {
    const all = [eng('claude'), eng('deepl'), eng('gemini', false), eng('ollama'), eng('google'), eng('test_offline')]
    expect(discoverEngines(all).map((e) => e.name)).toEqual(['claude', 'ollama', 'test_offline'])
  })
})

describe('text helpers', () => {
  it('detects Chinese like the server does', () => {
    expect(hasChinese('女将军')).toBe(true)
    expect(hasChinese('The General')).toBe(false)
    expect(hasChinese('ソード')).toBe(false)
  })

  it('labels media types and builds the card line', () => {
    expect(mediaLabel('audio_drama')).toBe('Audio drama')
    expect(mediaLabel('')).toBe('—')
    const t = { author: 'Mo', media_type: 'novel', language: 'zh', tags: 'gl' } as KnownTitle
    expect(titleMeta(t)).toBe('Mo · gl')
    expect(titleMeta({ ...t, author: null, tags: null })).toBe('Unknown author')
  })

  it('writes the count line', () => {
    expect(catalogCount(0, 0, '')).toBe('Your catalogue is empty.')
    expect(catalogCount(0, 3, 'moon')).toBe('No match for “moon” among your 3 saved titles.')
    expect(catalogCount(2, 3, 'moon')).toBe('2 of 3 saved titles matching “moon”')
    expect(catalogCount(1, 1, '')).toBe('1 of 1 saved title')
  })
})

describe('paginateUrls', () => {
  it('replaces every literal {page} and caps the range', () => {
    expect(paginateUrls('https://x.cn/t?p={page}&q={page}', 2, 3)).toEqual([
      'https://x.cn/t?p=2&q=2',
      'https://x.cn/t?p=3&q=3',
    ])
    expect(paginateUrls('https://x.cn/{page}', 1, 500)).toHaveLength(MAX_PAGINATE_PAGES)
  })

  it('returns nothing for a pattern without {page} or a bad range', () => {
    expect(paginateUrls('https://x.cn/', 1, 3)).toEqual([])
    expect(paginateUrls('https://x.cn/{page}', 0, 3)).toEqual([])
    expect(paginateUrls('https://x.cn/{page}', 4, 3)).toEqual([])
    expect(paginateUrls('https://x.cn/{page}', 1.5, 3)).toEqual([])
    expect(paginateUrls('https://x.cn/{page}', NaN, 3)).toEqual([])
  })

  it('does not treat other braces as placeholders', () => {
    expect(paginateUrls('https://x.cn/{0}/{page}', 1, 1)).toEqual(['https://x.cn/{0}/1'])
  })
})

describe('bulk urls', () => {
  it('parses one per line, trimmed and deduped', () => {
    expect(parseUrlList(' https://a.cn \n\nhttps://b.cn\r\nhttps://a.cn')).toEqual(['https://a.cn', 'https://b.cn'])
  })

  it('explains what is wrong', () => {
    expect(bulkUrlsProblem([])).toMatch(/at least one/)
    expect(bulkUrlsProblem(Array.from({ length: 11 }, (_, i) => `https://a.cn/${i}`))).toMatch(/At most 10/)
    expect(bulkUrlsProblem(['ftp://a.cn'])).toMatch(/Not a web address/)
    expect(bulkUrlsProblem(['https://a.cn'])).toBeNull()
  })
})

describe('commitEntries', () => {
  const entries: BulkEntry[] = [
    { title: 'A', author: 'x', tags: '', source_url: 'https://a.cn', has_audio_drama: true, entry_id: 'r-0' },
    { title: 'B', author: '', tags: 't', source_url: '', has_audio_drama: false },
  ]
  it('sends only the ticked entries, keeping entry_id when present', () => {
    expect(commitEntries(entries, new Set([0]))).toEqual([
      { title: 'A', author: 'x', tags: '', source_url: 'https://a.cn', has_audio_drama: true, language: 'zh', entry_id: 'r-0' },
    ])
    expect(commitEntries(entries, new Set([1]))[0]).not.toHaveProperty('entry_id')
    expect(commitEntries(entries, new Set())).toEqual([])
  })
})

describe('add a title', () => {
  it('fills blanks from a suggestion and keeps typed values it leaves empty', () => {
    const form = { ...EMPTY_TITLE, author: 'typed', language: 'ja' }
    const out = applySuggestion(form, { title_zh: '长公主', title_en: 'Princess', author: ' ', summary: 'S' }, ' https://p.cn/1 ')
    expect(out).toMatchObject({
      title_original: '长公主', title_en: 'Princess', author: 'typed', summary_en: 'S',
      source_url: 'https://p.cn/1', source_name: 'url', language: 'zh',
    })
    expect(applySuggestion(form, { title_en: 'E' }, '').language).toBe('ja')
  })

  it('names the fields that block saving', () => {
    expect(titleFormProblems(EMPTY_TITLE).title).toBeTruthy()
    expect(titleFormProblems({ ...EMPTY_TITLE, title_original: 'x', source_url: 'javascript:alert(1)' })).toEqual({
      url: 'The source URL must start with http:// or https://.',
    })
    expect(titleFormProblems({ ...EMPTY_TITLE, title_original: 'x' })).toEqual({})
  })
})

describe('links and errors', () => {
  it('only links http(s) addresses', () => {
    expect(safeHref('https://a.cn/x')).toBe('https://a.cn/x')
    expect(safeHref('javascript:alert(1)')).toBeNull()
    expect(safeHref('')).toBeNull()
    expect(safeHref(null)).toBeNull()
  })

  it('names a source by site, else by host', () => {
    expect(sourceLabel('jjwxc_baihe_tag', 'https://www.jjwxc.net/x')).toBe('jjwxc_baihe_tag')
    expect(sourceLabel('manual', 'https://example.cn/t/1')).toBe('example.cn')
    expect(sourceLabel('', 'not a url')).toBe('not a url')
  })

  it('reads the existing drama id from an import 409', () => {
    expect(existingDramaId({ status: 409, details: { drama_id: 4 } })).toBe(4)
    expect(existingDramaId({ status: 409, details: {} })).toBeNull()
    expect(existingDramaId({ status: 404, details: { drama_id: 4 } })).toBeNull()
    expect(existingDramaId(null)).toBeNull()
  })
})
