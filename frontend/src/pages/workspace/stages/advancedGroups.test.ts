import { describe, expect, it } from 'vitest'

import { ADVANCED_FIELD_IDS, advancedGroups, DEVELOPER_ONLY_FIELDS } from './advancedGroups'

describe('advancedGroups', () => {
  it('has the four groups in the owner order', () => {
    expect(advancedGroups(true).map((g) => g.label)).toEqual(['Speakers', 'Timing', 'Models and backends', 'Guards'])
  })

  it('places every control in exactly one group', () => {
    const placed = advancedGroups(true).flatMap((g) => g.fields)
    expect([...placed].sort()).toEqual([...ADVANCED_FIELD_IDS].sort())
    expect(new Set(placed).size).toBe(placed.length)
  })

  it('keeps all the controls (about twenty plus the prompt fields)', () => {
    expect(ADVANCED_FIELD_IDS.length).toBeGreaterThanOrEqual(24)
  })

  it('hides Developer Mode controls unless it is on', () => {
    const shown = advancedGroups(false).flatMap((g) => g.fields)
    for (const f of DEVELOPER_ONLY_FIELDS) expect(shown).not.toContain(f)
    expect(shown.length).toBe(ADVANCED_FIELD_IDS.length - DEVELOPER_ONLY_FIELDS.length)
    const all = advancedGroups(true).flatMap((g) => g.fields)
    for (const f of DEVELOPER_ONLY_FIELDS) expect(all).toContain(f)
  })

  it('never returns a group with nothing in it', () => {
    for (const dev of [true, false]) for (const g of advancedGroups(dev)) expect(g.fields.length).toBeGreaterThan(0)
  })

  it('opens only the first group the first time', () => {
    expect(advancedGroups(false).map((g) => g.defaultOpen)).toEqual([true, false, false, false])
  })

  it('gives each group its own remembered-fold key', () => {
    const keys = advancedGroups(true).map((g) => g.storageKey)
    expect(new Set(keys).size).toBe(keys.length)
    for (const k of keys) expect(k.startsWith('source.advanced.')).toBe(true)
  })
})
