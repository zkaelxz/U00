import { describe, expect, it } from 'vitest'
import { parseRoute } from '../router'
import { routeCrumbs } from './breadcrumbs'

const crumbs = (hash: string, opts = {}) => routeCrumbs(parseRoute(hash), opts)

describe('routeCrumbs', () => {
  it('is Library > title > stage inside a title', () => {
    expect(crumbs('#/drama/3/review', { title: 'Night Market' })).toEqual([
      { label: 'Library', href: '#/library' },
      { label: 'Night Market', href: '#/drama/3' },
      { label: 'Review' },
    ])
  })

  it('uses the stage the workspace resolved when the URL names none', () => {
    expect(crumbs('#/drama/3', { title: 'T', stage: 'translate' }).map((c) => c.label)).toEqual(['Library', 'T', 'Translate'])
    expect(crumbs('#/drama/3', { title: 'T' }).map((c) => c.label)).toEqual(['Library', 'T'])
  })

  it('falls back to a numbered placeholder until the title loads', () => {
    expect(crumbs('#/drama/3/export').map((c) => c.label)).toEqual(['Library', 'Title #3', 'Export'])
  })

  it('names the reader and the comic page after the title', () => {
    expect(crumbs('#/read/4', { title: 'A' }).map((c) => c.label)).toEqual(['Library', 'A', 'Reader'])
    expect(crumbs('#/comic/4', { title: 'A' }).map((c) => c.label)).toEqual(['Library', 'A', 'Comic'])
  })

  it('covers nested pages from the registry labels', () => {
    expect(crumbs('#/library-tools')).toEqual([{ label: 'Library', href: '#/library' }, { label: 'Library tools' }])
    expect(crumbs('#/settings?section=developer-mode')).toEqual([{ label: 'Settings', href: '#/settings' }, { label: 'Developer mode' }])
    expect(crumbs('#/benchmark')).toEqual([{ label: 'Diagnostics', href: '#/diagnostics' }, { label: 'Benchmark Lab' }])
  })

  it('has none for top-level destinations and unknown routes', () => {
    for (const h of ['#/library', '#/sources', '#/settings', '#/jobs', '#/diagnostics', '#/nonsense', '']) expect(crumbs(h)).toEqual([])
  })

  it('leaves a long title whole; the stylesheet cuts it', () => {
    const long = 'x'.repeat(300)
    expect(crumbs('#/drama/3/review', { title: long })[1].label).toBe(long)
  })
})
