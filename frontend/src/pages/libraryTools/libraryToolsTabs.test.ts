import { describe, expect, it } from 'vitest'

import {
  CARDS, LIBRARY_TOOLS_TABS, SHORT_TAB_MAX, cardStartsOpen, cardStorageKey, cardsOf, isLibraryToolsTab,
  type LibraryToolsCard,
} from './libraryToolsTabs'

const ALL = Object.keys(CARDS) as LibraryToolsCard[]

describe('Library tools tabs', () => {
  it('lists the five tabs in order', () => {
    expect(LIBRARY_TOOLS_TABS.map((t) => t.label)).toEqual(['Organize', 'Activity', 'Backup', 'Storage', 'Data folder'])
  })

  it('puts every card on exactly one tab, and no tab is empty', () => {
    const seen = LIBRARY_TOOLS_TABS.flatMap((t) => cardsOf(t.id))
    expect([...seen].sort()).toEqual([...ALL].sort())
    expect(new Set(seen).size).toBe(ALL.length)
    for (const t of LIBRARY_TOOLS_TABS) expect(cardsOf(t.id).length).toBeGreaterThan(0)
  })

  it('maps the cards the owner named to their tabs', () => {
    expect(cardsOf('backup').map((c) => CARDS[c].title)).toEqual([
      'Export', 'Backup', 'Backup of just my stuff', 'Restore', 'Automatic backup copies',
    ])
    expect(cardsOf('storage').map((c) => CARDS[c].title)).toEqual(['Cleanup', 'Unused voice clips', 'Trash', 'Temp files'])
    expect(cardsOf('activity').map((c) => CARDS[c].title)).toEqual(['Cost by title', 'Reading history'])
  })

  it('opens every card of a short tab', () => {
    for (const t of LIBRARY_TOOLS_TABS) {
      if (cardsOf(t.id).length <= SHORT_TAB_MAX) expect(cardsOf(t.id).every(cardStartsOpen)).toBe(true)
    }
  })

  it('opens only 1 to 3 cards of a longer tab', () => {
    for (const t of LIBRARY_TOOLS_TABS) {
      const cards = cardsOf(t.id)
      if (cards.length <= SHORT_TAB_MAX) continue
      const open = cards.filter(cardStartsOpen).length
      expect(open).toBeGreaterThanOrEqual(1)
      expect(open).toBeLessThanOrEqual(3)
    }
  })

  it('keeps the remembered-state keys of the cards that already existed', () => {
    expect(cardStorageKey('series')).toBe('library.tools.series')
    expect(cardStorageKey('voice-bank')).toBe('library.tools.voice-bank')
    expect(cardStorageKey('cost')).toBe('library.tools.cost-by-title')
    expect(new Set(ALL.map(cardStorageKey)).size).toBe(ALL.length)
  })

  it('recognises tab ids', () => {
    expect(isLibraryToolsTab('storage')).toBe(true)
    expect(isLibraryToolsTab('disk-usage')).toBe(false)
  })
})
