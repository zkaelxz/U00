import { describe, expect, it } from 'vitest'

import { parseRoute, routeHref } from './router'

describe('parseRoute', () => {
  it('defaults to the library', () => {
    for (const h of ['', '#', '#/', '#/library', '#/nope', '#/settings/extra', '#/drama', '#/drama/abc', '#/drama/0/source', '#/drama/1/a/b']) {
      expect(parseRoute(h)).toEqual({ name: 'library' })
    }
  })

  it('parses settings and diagnostics', () => {
    expect(parseRoute('#/settings')).toEqual({ name: 'settings' })
    expect(parseRoute('#/diagnostics')).toEqual({ name: 'diagnostics' })
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

  it('parses drama id and stage; no stage means null (the drama\'s current stage)', () => {
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

  it('round-trips through routeHref', () => {
    for (const r of [{ name: 'library' }, { name: 'settings' }, { name: 'diagnostics' }, { name: 'sources' }, { name: 'discover' }, { name: 'assistant' }, { name: 'drama', id: 4, stage: 'export' }, { name: 'drama', id: 4, stage: null }] as const) {
      expect(parseRoute(routeHref(r))).toEqual(r)
    }
  })
})
