import { describe, expect, it } from 'vitest'
import settingsSource from '../Settings.tsx?raw'
import { cardStartsOpen, filterSettings, SETTINGS_INDEX, SETTINGS_TABS, tabOfCard } from './settingsIndex'

const all = new Set(SETTINGS_INDEX.map((entry) => entry.cardId))

describe('settings registry', () => {
  it('has an entry for every card on the page', () => {
    const wrapped = [...settingsSource.matchAll(/<SettingsCard id="([^"]+)"/g)].map((m) => m[1])
    expect(wrapped.length).toBeGreaterThan(0)
    expect(wrapped.filter((id) => !all.has(id))).toEqual([])
    expect([...all].filter((id) => !wrapped.includes(id))).toEqual([])
  })

  it('wraps every card component in a SettingsCard', () => {
    const cards = settingsSource.match(/<(?!SettingsCard)\w*(Card|Section)\b/g) ?? []
    const wrappers = settingsSource.match(/<SettingsCard\b/g) ?? []
    // Settings' own inline Card (Jobs and GPU) sits inside a wrapper too.
    expect(cards.length).toBe(wrappers.length)
  })
})

describe('settings tabs', () => {
  const placed = SETTINGS_TABS.flatMap((t) => t.cards.map((c) => c.id))

  it('puts every card on exactly one tab', () => {
    expect(new Set(placed).size).toBe(placed.length)
    expect([...new Set(SETTINGS_INDEX.map((entry) => entry.cardId))].sort()).toEqual([...placed].sort())
    for (const id of placed) expect(tabOfCard(id)).toBeDefined()
  })

  it('has the six tabs, and the card each page section shows on its tab', () => {
    expect(SETTINGS_TABS.map((t) => t.label)).toEqual([
      'Translation and keys',
      'Costs',
      'Notifications and backups',
      'Sharing and devices',
      'Integrations',
      'System',
    ])
    expect(tabOfCard('notifications')).toBe('notifications')
    expect(tabOfCard('spend-history')).toBe('costs')
    expect(tabOfCard('past-costs')).toBe('costs')
    expect(tabOfCard('extension')).toBe('integrations')
    expect(tabOfCard('performance')).toBe('system')
    expect(tabOfCard('ports')).toBeUndefined()
    expect(tabOfCard('loaded-models')).toBeUndefined()
  })

  it('opens only the first card of every tab', () => {
    for (const tab of SETTINGS_TABS) {
      expect(tab.cards.filter((c) => cardStartsOpen(c.id)).map((c) => c.id)).toEqual([tab.cards[0].id])
    }
    expect(cardStartsOpen('notifications')).toBe(true)
    expect(cardStartsOpen('app-updates')).toBe(false)
    expect(cardStartsOpen('not-a-card')).toBe(false)
  })

  it('keeps the four cards a member sees together on the Sharing and devices tab', () => {
    expect(SETTINGS_TABS.find((t) => t.id === 'sharing')!.cards.map((c) => c.id)).toEqual(['sharing', 'customize-menu', 'devices', 'extension-devices'])
  })
})

describe('filterSettings', () => {
  it('finds the cards the old names pointed at', () => {
    const where = (q: string) => [...filterSettings(q, all).cardIds]
    expect(where('customize menu')).toEqual(['customize-menu'])
    expect(where('browser extension devices')).toEqual(['extension-devices'])
    expect(where('transcription experiments')).toEqual(['transcription-experiments'])
    expect(where('batch resume')).toEqual(['performance'])
    expect(where('keep free')).toEqual(['performance'])
    expect(where('notify me')).toEqual(['notifications'])
    expect(where('tesseract')).toContain('advanced')
    expect(where('ports')).toEqual([])
  })

  it('counts hits on the tab that holds each card', () => {
    const m = filterSettings('spend', all)
    expect(m.counts.costs).toBeGreaterThan(0)
    expect(m.counts.system).toBe(0)
  })

  it('finds the Advanced card by an Ollama field and counts it on System', () => {
    const m = filterSettings('ollama', all)
    expect(m.cardIds.has('advanced')).toBe(true)
    expect(m.counts.system).toBeGreaterThan(0)
    expect(m.total).toBe(m.cardIds.size)
  })

  it('needs every word to match, ignoring case', () => {
    expect(filterSettings('OLLAMA url', all).cardIds.has('advanced')).toBe(true)
    expect(filterSettings('ollama zzz', all).total).toBe(0)
  })

  it('matches nothing for an empty query and ignores cards that are not on the page', () => {
    expect(filterSettings('  ', all).total).toBe(0)
    expect(filterSettings('ollama', new Set(['sharing'])).total).toBe(0)
  })
})
