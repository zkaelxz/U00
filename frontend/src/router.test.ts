import { describe, expect, it } from 'vitest'

import { parseRoute, routeHref } from './router'

describe('parseRoute', () => {
  it('parses the Library tools page', () => {
    expect(parseRoute('#/library-tools')).toEqual({ name: 'library-tools' })
    expect(parseRoute('#/library-tools/x')).toEqual({ name: 'library' })
    expect(parseRoute('#/library-tools?tab=storage')).toEqual({ name: 'library-tools', tab: 'storage' })
    expect(parseRoute('#/library-tools?tab=disk-usage')).toEqual({ name: 'library-tools' })
    expect(routeHref({ name: 'library-tools', tab: 'data' })).toBe('#/library-tools?tab=data')
  })

  it('defaults to the library', () => {
    for (const h of ['', '#', '#/', '#/library', '#/nope', '#/settings/extra', '#/drama', '#/drama/abc', '#/drama/0/source', '#/drama/1/a/b']) {
      expect(parseRoute(h)).toEqual({ name: 'library' })
    }
  })

  it('parses settings and diagnostics', () => {
    expect(parseRoute('#/settings')).toEqual({ name: 'settings' })
    expect(parseRoute('#/settings?section=developer-mode')).toEqual({ name: 'settings', section: 'developer-mode' })
    expect(parseRoute('#/drama/3/translate?focus=glossary')).toEqual({ name: 'drama', id: 3, stage: 'translate', focus: 'glossary' })
    expect(parseRoute('#/drama/3/translate?focus=nope')).toEqual({ name: 'drama', id: 3, stage: 'translate' })
    expect(parseRoute('#/drama/3/review?focus=flagged')).toEqual({ name: 'drama', id: 3, stage: 'review', focus: 'flagged' })
    expect(routeHref({ name: 'drama', id: 3, stage: 'review', focus: 'flagged' })).toBe('#/drama/3/review?focus=flagged')
    expect(routeHref({ name: 'drama', id: 3, stage: 'translate', focus: 'characters' })).toBe('#/drama/3/translate?focus=characters')
    expect(parseRoute('#/drama/3/review?tools=coverage')).toEqual({ name: 'drama', id: 3, stage: 'review', tools: 'coverage' })
    expect(parseRoute('#/drama/3/review?tools=nope')).toEqual({ name: 'drama', id: 3, stage: 'review' })
    expect(parseRoute('#/drama/3/review?focus=flagged&tools=fix')).toEqual({ name: 'drama', id: 3, stage: 'review', focus: 'flagged', tools: 'fix' })
    expect(routeHref({ name: 'drama', id: 3, stage: 'review', tools: 'history' })).toBe('#/drama/3/review?tools=history')
    expect(routeHref({ name: 'drama', id: 3, stage: 'review', focus: 'flagged', tools: 'fix' })).toBe('#/drama/3/review?focus=flagged&tools=fix')
    expect(parseRoute('#/settings?section=uploads')).toEqual({ name: 'settings', section: 'uploads' })
    expect(parseRoute('#/settings?section=nope')).toEqual({ name: 'settings' })
    expect(routeHref({ name: 'settings', section: 'developer-mode' })).toBe('#/settings?section=developer-mode')
    expect(parseRoute('#/jobs')).toEqual({ name: 'jobs' })
    expect(parseRoute('#/jobs/extra')).toEqual({ name: 'library' })
    expect(parseRoute('#/diagnostics')).toEqual({ name: 'diagnostics' })
    expect(parseRoute('#/diagnostics?card=ports')).toEqual({ name: 'diagnostics', card: 'ports' })
    expect(parseRoute('#/diagnostics?tab=models')).toEqual({ name: 'diagnostics', tab: 'models' })
    expect(parseRoute('#/diagnostics?install=transcription')).toEqual({ name: 'diagnostics', card: 'packages' })
    expect(parseRoute('#/diagnostics?card=nope&tab=nope')).toEqual({ name: 'diagnostics' })
    expect(routeHref({ name: 'diagnostics', card: 'models' })).toBe('#/diagnostics?card=models')
    expect(routeHref({ name: 'diagnostics', tab: 'system' })).toBe('#/diagnostics?tab=system')
    expect(parseRoute('#/benchmark')).toEqual({ name: 'benchmark' })
    expect(parseRoute('#/benchmark/extra')).toEqual({ name: 'library' })
    expect(parseRoute('#/translate')).toEqual({ name: 'translate' })
    expect(parseRoute('#/translate/extra')).toEqual({ name: 'library' })
    expect(parseRoute('#/sources')).toEqual({ name: 'sources' })
    expect(parseRoute('#/sources/extra')).toEqual({ name: 'library' })
    expect(parseRoute('#/discover')).toEqual({ name: 'discover' })
    expect(parseRoute('#/discover/x')).toEqual({ name: 'library' })
    expect(parseRoute('#/live')).toEqual({ name: 'live' })
    expect(parseRoute('#/live/extra')).toEqual({ name: 'library' })
    expect(parseRoute('#/assistant')).toEqual({ name: 'assistant' })
    expect(parseRoute('#/assistant/extra')).toEqual({ name: 'library' })
    expect(routeHref({ name: 'assistant' })).toBe('#/assistant')
  })

  it('parses title id and stage; no stage means null (the title\'s current stage)', () => {
    expect(parseRoute('#/drama/12/review')).toEqual({ name: 'drama', id: 12, stage: 'review' })
    expect(parseRoute('#/drama/12')).toEqual({ name: 'drama', id: 12, stage: null })
    expect(routeHref({ name: 'drama', id: 12, stage: null })).toBe('#/drama/12')
    expect(parseRoute('#/drama/12/review?x=1')).toEqual({ name: 'drama', id: 12, stage: 'review' })
    expect(parseRoute('#/drama/1/%E0%A4%A')).toEqual({ name: 'library' })
  })

  it('parses the reader route with an optional page', () => {
    expect(parseRoute('#/read/3')).toEqual({ name: 'read', id: 3, page: null })
    expect(parseRoute('#/read/3?page=2')).toEqual({ name: 'read', id: 3, page: 2 })
    expect(parseRoute('#/read/3?page=0')).toEqual({ name: 'read', id: 3, page: null })
    expect(parseRoute('#/read/3?page=x')).toEqual({ name: 'read', id: 3, page: null })
    for (const h of ['#/read', '#/read/0', '#/read/abc', '#/read/3/extra']) {
      expect(parseRoute(h)).toEqual({ name: 'library' })
    }
    expect(routeHref({ name: 'read', id: 3, page: 2 })).toBe('#/read/3?page=2')
    expect(routeHref({ name: 'read', id: 3, page: null })).toBe('#/read/3')
  })

  it('parses the saved manga routes, names %-encoded', () => {
    expect(parseRoute('#/manga')).toEqual({ name: 'manga' })
    expect(parseRoute('#/manga/MangaK/Test%20Camp')).toEqual({ name: 'manga-series', source: 'MangaK', series: 'Test Camp' })
    expect(parseRoute('#/manga/MangaK/Test%20Camp/0001%20Ch%2F1?page=3')).toEqual({
      name: 'manga-read', source: 'MangaK', series: 'Test Camp', chapter: '0001 Ch/1', page: 3,
    })
    expect(parseRoute('#/manga/a/b/c?page=0')).toEqual({ name: 'manga-read', source: 'a', series: 'b', chapter: 'c', page: null })
    for (const h of ['#/manga/a', '#/manga/a/b/c/d', '#/manga/%E0%A4%A/b']) {
      expect(parseRoute(h)).toEqual({ name: 'library' })
    }
    const read = { name: 'manga-read', source: 'MangaK', series: 'Test Camp', chapter: '0001 Ch/1', page: 3 } as const
    expect(routeHref(read)).toBe('#/manga/MangaK/Test%20Camp/0001%20Ch%2F1?page=3')
    expect(parseRoute(routeHref(read))).toEqual(read)
    expect(routeHref({ name: 'manga-series', source: 'a b', series: '?' })).toBe('#/manga/a%20b/%3F')
    expect(routeHref({ name: 'manga' })).toBe('#/manga')
  })

  it('parses the comic route with an optional page', () => {
    expect(parseRoute('#/comic/3?page=2')).toEqual({ name: 'comic', id: 3, page: 2 })
    expect(parseRoute('#/comic/3')).toEqual({ name: 'comic', id: 3, page: null })
    expect(parseRoute('#/comic/3?page=0')).toEqual({ name: 'comic', id: 3, page: null })
    for (const h of ['#/comic', '#/comic/0', '#/comic/x', '#/comic/3/extra']) {
      expect(parseRoute(h)).toEqual({ name: 'library' })
    }
    expect(routeHref({ name: 'comic', id: 3, page: 2 })).toBe('#/comic/3?page=2')
    expect(routeHref({ name: 'comic', id: 3, page: null })).toBe('#/comic/3')
  })

  it('keeps the benchmark compare query raw; the plain route still works', () => {
    expect(parseRoute('#/benchmark?compare=claude:claude-sonnet-4-6,claude:claude-sonnet-5')).toEqual({
      name: 'benchmark', compare: 'claude:claude-sonnet-4-6,claude:claude-sonnet-5',
    })
    expect(parseRoute('#/benchmark?x=1&compare=ollama:qwen3%3A8b')).toEqual({ name: 'benchmark', compare: 'ollama:qwen3%3A8b' })
    expect(parseRoute('#/benchmark?compare=')).toEqual({ name: 'benchmark' })
    expect(parseRoute('#/benchmark?other=1')).toEqual({ name: 'benchmark' })
    expect(parseRoute(`#/benchmark?compare=${'a'.repeat(1001)}`)).toEqual({ name: 'benchmark' })
    expect(parseRoute('#/benchmark/x?compare=a:b')).toEqual({ name: 'library' })
    expect(routeHref({ name: 'benchmark', compare: 'a:b,a:c' })).toBe('#/benchmark?compare=a:b,a:c')
    expect(routeHref({ name: 'benchmark' })).toBe('#/benchmark')
    expect(parseRoute(routeHref({ name: 'benchmark', compare: 'ollama:qwen3%3A8b' }))).toEqual({ name: 'benchmark', compare: 'ollama:qwen3%3A8b' })
  })

  it('round-trips through routeHref', () => {
    for (const r of [{ name: 'library' }, { name: 'jobs' }, { name: 'settings' }, { name: 'diagnostics' }, { name: 'benchmark' }, { name: 'sources' }, { name: 'discover' }, { name: 'assistant' }, { name: 'drama', id: 4, stage: 'export' }, { name: 'drama', id: 4, stage: null }] as const) {
      expect(parseRoute(routeHref(r))).toEqual(r)
    }
  })
})
