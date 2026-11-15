import { describe, expect, it } from 'vitest'
import settingsSource from '../Settings.tsx?raw'
import { settingsCardStartsOpen, filterElsewhere, filterSettings, SETTINGS_INDEX, SETTINGS_TABS, tabOfCard } from './settingsIndex'

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

  it('has the seven tabs, and the card each page section shows on its tab', () => {
    expect(SETTINGS_TABS.map((t) => t.label)).toEqual([
      'Engines and keys',
      'Costs',
      'Transcription and jobs',
      'Connections',
      'Updates',
      'My account and look',
      'Advanced',
    ])
    expect(tabOfCard('notifications')).toBe('connections')
    expect(tabOfCard('app-updates')).toBe('updates')
    expect(tabOfCard('source-update')).toBe('updates')
    expect(tabOfCard('spend-history')).toBe('costs')
    expect(tabOfCard('past-costs')).toBe('costs')
    expect(tabOfCard('extension')).toBe('connections')
    expect(tabOfCard('performance')).toBe('transcription')
    expect(tabOfCard('developer-mode')).toBe('advanced')
    expect(tabOfCard('ports')).toBeUndefined()
    expect(tabOfCard('loaded-models')).toBeUndefined()
  })

  it('opens only the first card of every tab', () => {
    for (const tab of SETTINGS_TABS) {
      // The Updates tab has two cards of which a copy shows only one, so both start open.
      expect(tab.cards.filter((c) => settingsCardStartsOpen(c.id)).map((c) => c.id)).toEqual(tab.id === 'updates' ? ['app-updates', 'source-update'] : [tab.cards[0].id])
    }
    expect(settingsCardStartsOpen('notifications')).toBe(true)
    expect(settingsCardStartsOpen('app-updates')).toBe(true)
    expect(settingsCardStartsOpen('source-update')).toBe(true)
    expect(settingsCardStartsOpen('display')).toBe(false)
    expect(settingsCardStartsOpen('not-a-card')).toBe(false)
  })

  it('keeps the cards of the My account and look tab in order', () => {
    // The tab members see: every card here works for any signed-in person.
    expect(SETTINGS_TABS.find((t) => t.id === 'account')!.cards.map((c) => c.id)).toEqual(['sharing', 'display', 'customize-menu', 'devices', 'extension-devices', 'ollama-key'])
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
    expect(m.counts.advanced).toBe(0)
  })

  it('finds the Advanced card by an Ollama field and counts it on the Advanced tab', () => {
    const m = filterSettings('ollama', all)
    expect(m.cardIds.has('advanced')).toBe(true)
    expect(m.counts.advanced).toBeGreaterThan(0)
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

describe('filterElsewhere', () => {
  const everyone = () => true
  const labels = (q: string, allowed: Parameters<typeof filterElsewhere>[1] = everyone) =>
    filterElsewhere(q, allowed).map((entry) => `${entry.page}: ${entry.label}`)

  it('points a search for models at Diagnostics, where the model cards live', () => {
    expect(labels('models')).toContain('Diagnostics: Models')
    expect(filterElsewhere('loaded models', everyone).map((entry) => entry.to)).toEqual([{ name: 'diagnostics', card: 'loaded-models' }])
  })

  it('points a search for backup at the Library tools backup tab', () => {
    expect(filterElsewhere('backup', everyone).map((entry) => entry.to)).toEqual([{ name: 'library-tools', tab: 'backup' }])
  })

  it('leaves out what the caller says this person cannot open', () => {
    expect(labels('models', (entry) => entry.page !== 'Diagnostics')).toEqual([])
  })

  it('matches nothing for an empty query', () => {
    expect(filterElsewhere('  ', everyone)).toEqual([])
  })
})
