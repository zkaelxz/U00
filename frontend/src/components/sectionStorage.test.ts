import { describe, expect, it } from 'vitest'
import { readSectionOpen, sectionStorageKey, writeSectionOpen, type StorageLike } from './sectionStorage'

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
