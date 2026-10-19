import { describe, expect, it } from 'vitest'

import { parseRoute, routeHref } from '../../router'
import { hashWithTab } from './mediaTabs'
import {
  TRANSLATE_TABS,
  isTranslateTab,
  readTranslateTab,
  resolveTranslateTab,
  tabForFocus,
  writeTranslateTab,
} from './translateTabs'

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

describe('translate tabs', () => {
  it('are the four the owner named, in order', () => {
    expect(TRANSLATE_TABS.map((t) => t.label)).toEqual(['Translate', 'Glossary', 'Characters', 'Original novel'])
    expect(TRANSLATE_TABS.map((t) => t.id)).toEqual(['translate', 'glossary', 'characters', 'novel'])
  })

  it('accepts exactly those ids', () => {
    for (const id of ['translate', 'glossary', 'characters', 'novel']) expect(isTranslateTab(id)).toBe(true)
    for (const id of ['add', 'tools', '', null, 3]) expect(isTranslateTab(id)).toBe(false)
  })
})

describe('resolveTranslateTab', () => {
  it('uses the wanted tab, else Translate', () => {
    expect(resolveTranslateTab('glossary')).toBe('glossary')
    expect(resolveTranslateTab('bogus')).toBe('translate')
    expect(resolveTranslateTab(null)).toBe('translate')
    expect(resolveTranslateTab(undefined)).toBe('translate')
  })
})

describe('tabForFocus', () => {
  it('sends the panel a ?focus= link points at to its tab', () => {
    expect(tabForFocus('glossary')).toBe('glossary')
    expect(tabForFocus('characters')).toBe('characters')
    expect(tabForFocus('flagged')).toBeNull()
    expect(tabForFocus(undefined)).toBeNull()
  })
})

describe('remembered tab', () => {
  it('remembers the last tab per title', () => {
    const s = memoryStorage()
    writeTranslateTab(s, 4, 'glossary')
    writeTranslateTab(s, 5, 'novel')
    expect(readTranslateTab(s, 4)).toBe('glossary')
    expect(readTranslateTab(s, 5)).toBe('novel')
    expect(readTranslateTab(s, 6)).toBeNull()
  })

  it('ignores a stored value that is not a tab', () => {
    const s = memoryStorage()
    s.setItem('baihe.pref.translate.tab.4', JSON.stringify('nope'))
    expect(readTranslateTab(s, 4)).toBeNull()
  })

  it('works without storage, and with storage that throws', () => {
    expect(readTranslateTab(null, 4)).toBeNull()
    expect(writeTranslateTab(null, 4, 'glossary')).toBe(false)
    const broken = {
      getItem: () => {
        throw new Error('blocked')
      },
      setItem: () => {
        throw new Error('blocked')
      },
    }
    expect(readTranslateTab(broken, 4)).toBeNull()
    expect(writeTranslateTab(broken, 4, 'glossary')).toBe(false)
  })
})

describe('deep link', () => {
  it('parses ?tab= on the translate stage', () => {
    expect(parseRoute('#/drama/7/translate?tab=glossary')).toEqual({ name: 'drama', id: 7, stage: 'translate', tab: 'glossary' })
    expect(parseRoute('#/drama/7/translate?tab=novel')).toEqual({ name: 'drama', id: 7, stage: 'translate', tab: 'novel' })
  })

  it('drops a tab that belongs to the other stage, or to none', () => {
    expect(parseRoute('#/drama/7/translate?tab=tools')).toEqual({ name: 'drama', id: 7, stage: 'translate' })
    expect(parseRoute('#/drama/7/translate?tab=nope')).toEqual({ name: 'drama', id: 7, stage: 'translate' })
    expect(parseRoute('#/drama/7/source?tab=glossary')).toEqual({ name: 'drama', id: 7, stage: 'source' })
  })

  it('still parses the Media stage tab', () => {
    expect(parseRoute('#/drama/7/source?tab=credits')).toEqual({ name: 'drama', id: 7, stage: 'source', tab: 'details' })
  })

  it('writes the tab back, next to a focus', () => {
    expect(routeHref({ name: 'drama', id: 7, stage: 'translate', tab: 'characters' })).toBe('#/drama/7/translate?tab=characters')
    expect(routeHref({ name: 'drama', id: 7, stage: 'translate', focus: 'glossary', tab: 'glossary' })).toBe('#/drama/7/translate?focus=glossary&tab=glossary')
  })

  it('hashWithTab keeps a focus already in the address', () => {
    expect(hashWithTab('#/drama/7/translate?focus=glossary', 'characters')).toBe('#/drama/7/translate?focus=glossary&tab=characters')
  })
})
