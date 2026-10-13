import { describe, expect, it } from 'vitest'

import { ADVANCED_FIELD_IDS, advancedGroups, DEVELOPER_ONLY_FIELDS } from './advancedGroups'

describe('advancedGroups', () => {
  it('has the groups in the owner order, Switches last', () => {
    expect(advancedGroups(true).map((g) => g.label)).toEqual(['Speakers', 'Timing', 'Models and backends', 'Guards', 'Switches'])
  })

  it('places every control in exactly one group', () => {
    const placed = advancedGroups(true).flatMap((g) => g.fields)
    expect([...placed].sort()).toEqual([...ADVANCED_FIELD_IDS].sort())
    expect(new Set(placed).size).toBe(placed.length)
  })

  it('keeps all the controls', () => {
    expect(ADVANCED_FIELD_IDS.length).toBeGreaterThanOrEqual(25)
  })

  it('puts the eight switches in one card', () => {
    const switches = advancedGroups(true).find((g) => g.id === 'switches')!
    expect(switches.fields).toHaveLength(8)
    expect(advancedGroups(false).find((g) => g.id === 'switches')!.fields).toHaveLength(7)
  })

  it('hides Developer Mode controls unless it is on, and drops the group that leaves empty', () => {
    const shown = advancedGroups(false).flatMap((g) => g.fields)
    for (const f of DEVELOPER_ONLY_FIELDS) expect(shown).not.toContain(f)
    expect(shown.length).toBe(ADVANCED_FIELD_IDS.length - DEVELOPER_ONLY_FIELDS.length)
    expect(advancedGroups(false).map((g) => g.id)).not.toContain('guards')
    const all = advancedGroups(true).flatMap((g) => g.fields)
    for (const f of DEVELOPER_ONLY_FIELDS) expect(all).toContain(f)
  })

  it('never returns a group with nothing in it', () => {
    for (const dev of [true, false]) for (const g of advancedGroups(dev)) expect(g.fields.length).toBeGreaterThan(0)
  })

  it('opens exactly one fold the first time: Switches until the options are saved, then Speakers', () => {
    for (const dev of [true, false]) {
      expect(advancedGroups(dev, false).filter((g) => g.defaultOpen).map((g) => g.id)).toEqual(['switches'])
      expect(advancedGroups(dev, true).filter((g) => g.defaultOpen).map((g) => g.id)).toEqual(['speakers'])
    }
  })

  it('gives each group its own remembered-fold key', () => {
    const keys = advancedGroups(true).map((g) => g.storageKey)
    expect(new Set(keys).size).toBe(keys.length)
    for (const k of keys) expect(k.startsWith('source.advanced.')).toBe(true)
  })
})
