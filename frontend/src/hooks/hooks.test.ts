import { describe, expect, it } from 'vitest'

import { PREF_KEY_PREFIX, readPref, writePref } from './usePersistedState'
import { comboOf, isTextEntry, type KeyLike } from './useShortcut'

const k = (key: string, mods: Partial<KeyLike> = {}): KeyLike => ({
  key, altKey: false, ctrlKey: false, metaKey: false, shiftKey: false, ...mods,
})

describe('comboOf', () => {
  it('lowercases letters and keeps printable symbols', () => {
    expect(comboOf(k('J', { shiftKey: true }))).toBe('j')
    expect(comboOf(k('?', { shiftKey: true }))).toBe('?')
    expect(comboOf(k(']'))).toBe(']')
  })
  it('names modifiers for named keys', () => {
    expect(comboOf(k('Delete', { shiftKey: true }))).toBe('shift+delete')
    expect(comboOf(k('ArrowDown', { altKey: true }))).toBe('alt+arrowdown')
    expect(comboOf(k(' ', { altKey: true }))).toBe('alt+ ')
    expect(comboOf(k(' ', { altKey: true }))).toBe('alt+ ')
    expect(comboOf(k('Enter'))).toBe('enter')
  })
  it('ignores Ctrl/Meta, IME composition and bare modifiers', () => {
    expect(comboOf(k('s', { ctrlKey: true }))).toBeNull()
    expect(comboOf(k('s', { metaKey: true }))).toBeNull()
    expect(comboOf(k('j', { isComposing: true }))).toBeNull()
    expect(comboOf(k('Process'))).toBeNull()
    expect(comboOf(k('Shift', { shiftKey: true }))).toBeNull()
  })
})

describe('isTextEntry', () => {
  it('knows text fields from other controls', () => {
    expect(isTextEntry({ tagName: 'TEXTAREA' } as unknown as EventTarget)).toBe(true)
    expect(isTextEntry({ tagName: 'INPUT', type: 'search' } as unknown as EventTarget)).toBe(true)
    expect(isTextEntry({ tagName: 'INPUT', type: 'checkbox' } as unknown as EventTarget)).toBe(false)
    expect(isTextEntry({ tagName: 'LI' } as unknown as EventTarget)).toBe(false)
    expect(isTextEntry({ tagName: 'DIV', isContentEditable: true } as unknown as EventTarget)).toBe(true)
    expect(isTextEntry(null)).toBe(false)
  })
})

describe('persisted preferences', () => {
  const store = () => {
    const m = new Map<string, string>()
    return { getItem: (key: string) => m.get(key) ?? null, setItem: (key: string, v: string) => void m.set(key, v), m }
  }
  it('round-trips JSON under the prefix', () => {
    const s = store()
    expect(writePref(s, 'review.loop', true)).toBe(true)
    expect(s.m.get(`${PREF_KEY_PREFIX}review.loop`)).toBe('true')
    expect(readPref(s, 'review.loop', false)).toBe(true)
  })
  it('falls back on missing, corrupt or wrongly typed values', () => {
    const s = store()
    expect(readPref(s, 'x', 3)).toBe(3)
    s.m.set(`${PREF_KEY_PREFIX}x`, '{nope')
    expect(readPref(s, 'x', 3)).toBe(3)
    s.m.set(`${PREF_KEY_PREFIX}x`, '"text"')
    expect(readPref(s, 'x', 3)).toBe(3)
    expect(readPref(null, 'x', 3)).toBe(3)
  })
  it('survives a throwing storage', () => {
    const bad = { getItem: () => { throw new Error('blocked') }, setItem: () => { throw new Error('blocked') } }
    expect(readPref(bad, 'x', 'a')).toBe('a')
    expect(writePref(bad, 'x', 'a')).toBe(false)
  })
})
