import { describe, expect, it } from 'vitest'

import { parseRoute, routeHref } from '../../router'
import {
  hashWithTab,
  isMediaTab,
  mediaTabsFor,
  readMediaTab,
  resolveMediaTab,
  writeMediaTab,
  type MediaTab,
} from './mediaTabs'
import type { TitleKind } from './titleKind'

const ids = (kind: TitleKind): MediaTab[] => mediaTabsFor(kind).map((t) => t.id)

function memoryStorage(initial: Record<string, string> = {}) {
  const data = { ...initial }
  return {
    data,
    getItem: (k: string) => data[k] ?? null,
    setItem: (k: string, v: string) => {
      data[k] = v
    },
  }
}

describe('mediaTabsFor', () => {
  it.each<TitleKind>(['video', 'audio', 'subtitle'])('%s titles get all four sub-tabs', (kind) => {
    expect(ids(kind)).toEqual(['add', 'transcribe', 'tools', 'details'])
  })

  it.each<TitleKind>(['novel', 'comic'])('%s titles hide Transcribe and Tools', (kind) => {
    expect(ids(kind)).toEqual(['add', 'details'])
  })

  it('labels the tabs as the owner named them', () => {
    expect(mediaTabsFor('video').map((t) => t.label)).toEqual(['Add media', 'Transcribe', 'Tools', 'Details'])
  })
})

describe('resolveMediaTab', () => {
  it('uses the wanted tab when this kind has it', () => {
    expect(resolveMediaTab('tools', 'video')).toBe('tools')
  })

  it('falls back to Add media for a tab the kind hides, or one that does not exist', () => {
    expect(resolveMediaTab('tools', 'novel')).toBe('add')
    expect(resolveMediaTab('transcribe', 'comic')).toBe('add')
    expect(resolveMediaTab('bogus', 'video')).toBe('add')
    expect(resolveMediaTab(null, 'video')).toBe('add')
    expect(resolveMediaTab(undefined, 'audio')).toBe('add')
  })

  it('keeps Details for a novel', () => {
    expect(resolveMediaTab('details', 'novel')).toBe('details')
  })
})

describe('remembered tab', () => {
  it('remembers the last tab per title', () => {
    const s = memoryStorage()
    writeMediaTab(s, 4, 'tools')
    writeMediaTab(s, 5, 'details')
    expect(readMediaTab(s, 4)).toBe('tools')
    expect(readMediaTab(s, 5)).toBe('details')
    expect(readMediaTab(s, 6)).toBeNull()
  })

  it('ignores a stored value that is not a tab', () => {
    const s = memoryStorage()
    s.setItem('baihe.pref.source.tab.4', JSON.stringify('nope'))
    expect(readMediaTab(s, 4)).toBeNull()
  })

  it('works without storage', () => {
    expect(readMediaTab(null, 4)).toBeNull()
    expect(writeMediaTab(null, 4, 'tools')).toBe(false)
  })

  it('survives storage that throws', () => {
    const broken = {
      getItem: () => {
        throw new Error('blocked')
      },
      setItem: () => {
        throw new Error('blocked')
      },
    }
    expect(readMediaTab(broken, 4)).toBeNull()
    expect(writeMediaTab(broken, 4, 'tools')).toBe(false)
  })
})

describe('isMediaTab', () => {
  it('accepts exactly the four ids', () => {
    for (const id of ['add', 'transcribe', 'tools', 'details']) expect(isMediaTab(id)).toBe(true)
    for (const id of ['source', '', null, 3]) expect(isMediaTab(id)).toBe(false)
  })
})

describe('deep link', () => {
  it('parses ?tab= on a workspace route', () => {
    expect(parseRoute('#/drama/7/source?tab=transcribe')).toEqual({ name: 'drama', id: 7, stage: 'source', tab: 'transcribe' })
  })

  it('drops an unknown tab', () => {
    expect(parseRoute('#/drama/7/source?tab=nope')).toEqual({ name: 'drama', id: 7, stage: 'source' })
  })

  it('writes the tab back into the hash', () => {
    expect(routeHref({ name: 'drama', id: 7, stage: 'source', tab: 'details' })).toBe('#/drama/7/source?tab=details')
  })

  it('keeps focus and tab together', () => {
    expect(routeHref({ name: 'drama', id: 7, stage: 'translate', focus: 'glossary' })).toBe('#/drama/7/translate?focus=glossary')
  })
})

describe('hashWithTab', () => {
  it('adds the tab to a bare stage hash', () => {
    expect(hashWithTab('#/drama/7/source', 'tools')).toBe('#/drama/7/source?tab=tools')
  })

  it('replaces a tab already there', () => {
    expect(hashWithTab('#/drama/7/source?tab=add', 'details')).toBe('#/drama/7/source?tab=details')
  })

  it('keeps other query values', () => {
    expect(hashWithTab('#/drama/7/source?autofill=1', 'details')).toBe('#/drama/7/source?autofill=1&tab=details')
  })
})
