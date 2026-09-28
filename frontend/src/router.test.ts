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
  })

  it('parses drama id and stage, defaulting the stage', () => {
    expect(parseRoute('#/drama/12/review')).toEqual({ name: 'drama', id: 12, stage: 'review' })
    expect(parseRoute('#/drama/12')).toEqual({ name: 'drama', id: 12, stage: 'source' })
    expect(parseRoute('#/drama/12/review?x=1')).toEqual({ name: 'drama', id: 12, stage: 'review' })
    expect(parseRoute('#/drama/1/%E0%A4%A')).toEqual({ name: 'library' })
  })

  it('round-trips through routeHref', () => {
    for (const r of [{ name: 'library' }, { name: 'settings' }, { name: 'diagnostics' }, { name: 'drama', id: 4, stage: 'export' }] as const) {
      expect(parseRoute(routeHref(r))).toEqual(r)
    }
  })
})
