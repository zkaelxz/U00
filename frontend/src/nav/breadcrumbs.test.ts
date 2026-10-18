import { describe, expect, it } from 'vitest'
import { parseRoute } from '../router'
import { routeCrumbs } from './breadcrumbs'

const crumbs = (hash: string, opts = {}) => routeCrumbs(parseRoute(hash), opts)

describe('routeCrumbs', () => {
  it('names the reader and the comic page after the title', () => {
    expect(crumbs('#/read/4', { title: 'A' }).map((c) => c.label)).toEqual(['Library', 'A', 'Reader'])
    expect(crumbs('#/comic/4', { title: 'A' }).map((c) => c.label)).toEqual(['Library', 'A', 'Comic'])
  })

  it('covers nested pages from the registry labels', () => {
    expect(crumbs('#/library-tools')).toEqual([{ label: 'Library', href: '#/library' }, { label: 'Library tools' }])
    expect(crumbs('#/sources/manage?tab=network')).toEqual([])
    expect(crumbs('#/settings?section=developer-mode')).toEqual([{ label: 'Settings', href: '#/settings' }, { label: 'Developer mode' }])
    expect(crumbs('#/benchmark')).toEqual([{ label: 'Diagnostics', href: '#/diagnostics' }, { label: 'Benchmark Lab' }])
  })

  it('has none for top-level destinations and unknown routes', () => {
    for (const h of ['#/library', '#/sources', '#/settings', '#/jobs', '#/diagnostics', '#/nonsense', '']) expect(crumbs(h)).toEqual([])
  })

  it('leaves a long title whole; the stylesheet cuts it', () => {
    const long = 'x'.repeat(300)
    expect(crumbs('#/read/3', { title: long })[1].label).toBe(long)
  })
})
