import { describe, expect, it } from 'vitest'
import { readSectionOpen, sectionStorageKey, seedTabFolds, writeSectionOpen, type SeedStorage, type StorageLike } from './sectionStorage'

function memory(): StorageLike & { data: Map<string, string> } {
  const data = new Map<string, string>()
  return { data, getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) }
}
const throwing: StorageLike = {
  getItem: () => { throw new Error('blocked') },
  setItem: () => { throw new Error('blocked') },
}

describe('section open-state storage', () => {
  it('prefixes keys', () => expect(sectionStorageKey('a.b')).toBe('baihe.section.a.b'))
  it('uses the fallback when nothing is stored', () => {
    expect(readSectionOpen(memory(), 'k', true)).toBe(true)
    expect(readSectionOpen(memory(), 'k', false)).toBe(false)
  })
  it('round-trips and overrides the fallback', () => {
    const s = memory()
    expect(writeSectionOpen(s, 'k', true)).toBe(true)
    expect(s.data.get('baihe.section.k')).toBe('1')
    expect(readSectionOpen(s, 'k', false)).toBe(true)
    writeSectionOpen(s, 'k', false)
    expect(readSectionOpen(s, 'k', true)).toBe(false)
  })
  it('ignores garbage values', () => {
    const s = memory()
    s.data.set('baihe.section.k', 'maybe')
    expect(readSectionOpen(s, 'k', true)).toBe(true)
  })
  it('survives missing or throwing storage', () => {
    expect(readSectionOpen(null, 'k', true)).toBe(true)
    expect(readSectionOpen(throwing, 'k', false)).toBe(false)
    expect(writeSectionOpen(null, 'k', true)).toBe(false)
    expect(writeSectionOpen(throwing, 'k', true)).toBe(false)
  })
})

describe('seedTabFolds', () => {
  function store(initial: Record<string, string> = {}): SeedStorage & { data: Map<string, string> } {
    const data = new Map(Object.entries(initial))
    return {
      data,
      getItem: (k) => data.get(k) ?? null,
      setItem: (k, v) => void data.set(k, v),
      removeItem: (k) => void data.delete(k),
    }
  }
  const keys = ['a', 'b', 'c']

  it('drops an all-collapsed state saved earlier so the first card opens by default', () => {
    const s = store({ [sectionStorageKey('a')]: '0', [sectionStorageKey('b')]: '0', [sectionStorageKey('c')]: '0' })
    seedTabFolds(s, 'page', 'tab', keys)
    expect(keys.map((k) => readSectionOpen(s, k, k === 'a'))).toEqual([true, false, false])
  })

  it('keeps folds the person already made, where something is open', () => {
    const s = store({ [sectionStorageKey('a')]: '0', [sectionStorageKey('b')]: '1' })
    seedTabFolds(s, 'page', 'tab', keys)
    expect(keys.map((k) => readSectionOpen(s, k, k === 'a'))).toEqual([false, true, false])
  })

  it('runs once per tab: collapsing everything afterwards stays collapsed', () => {
    const s = store()
    seedTabFolds(s, 'page', 'tab', keys)
    keys.forEach((k) => writeSectionOpen(s, k, false))
    seedTabFolds(s, 'page', 'tab', keys)
    expect(keys.map((k) => readSectionOpen(s, k, k === 'a'))).toEqual([false, false, false])
  })

  it('keeps tabs and pages apart, and survives blocked storage', () => {
    const s = store()
    seedTabFolds(s, 'page', 'one', keys)
    expect([...s.data.keys()]).toEqual(['baihe.tabfolds.page.one'])
    expect(() => seedTabFolds({ getItem: () => { throw new Error('blocked') }, setItem: () => undefined, removeItem: () => undefined }, 'p', 't', keys)).not.toThrow()
    expect(() => seedTabFolds(null, 'p', 't', keys)).not.toThrow()
  })
})
