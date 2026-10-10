import { describe, expect, it } from 'vitest'
import settingsSource from '../Settings.tsx?raw'
import { filterSettings, SETTINGS_INDEX } from './settingsIndex'

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
    // Settings' own inline Cards (Performance, Notifications, Spending, Ports) sit inside a wrapper too.
    expect(cards.length).toBe(wrappers.length)
  })
})

describe('filterSettings', () => {
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
