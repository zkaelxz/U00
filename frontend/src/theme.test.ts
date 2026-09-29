import { describe, expect, it } from 'vitest'

import { applyTheme, isDarkTheme, loadTheme, parseTheme, saveTheme, THEME_KEY, type ThemeStorage } from './theme'

function memory(): ThemeStorage & { data: Map<string, string> } {
  const data = new Map<string, string>()
  return {
    data,
    getItem: (k) => data.get(k) ?? null,
    setItem: (k, v) => void data.set(k, v),
    removeItem: (k) => void data.delete(k),
  }
}

const throwing: ThemeStorage = {
  getItem: () => {
    throw new Error('blocked')
  },
  setItem: () => {
    throw new Error('blocked')
  },
  removeItem: () => {
    throw new Error('blocked')
  },
}

describe('theme preference', () => {
  it('parses only light and dark, anything else is system', () => {
    expect(parseTheme('dark')).toBe('dark')
    expect(parseTheme('light')).toBe('light')
    expect(parseTheme('sepia')).toBe('system')
    expect(parseTheme(null)).toBe('system')
  })

  it('saves and loads; system removes the key', () => {
    const s = memory()
    saveTheme('dark', s)
    expect(s.data.get(THEME_KEY)).toBe('dark')
    expect(loadTheme(s)).toBe('dark')
    saveTheme('system', s)
    expect(s.data.has(THEME_KEY)).toBe(false)
    expect(loadTheme(s)).toBe('system')
  })

  it('survives storage that throws or is missing', () => {
    expect(loadTheme(throwing)).toBe('system')
    expect(() => saveTheme('dark', throwing)).not.toThrow()
    expect(loadTheme(null)).toBe('system')
  })

  it('applies data-theme on the root, and removes it for system', () => {
    const attrs = new Map<string, string>()
    const root = {
      setAttribute: (k: string, v: string) => void attrs.set(k, v),
      removeAttribute: (k: string) => void attrs.delete(k),
    } as unknown as HTMLElement
    applyTheme('dark', root)
    expect(attrs.get('data-theme')).toBe('dark')
    applyTheme('system', root)
    expect(attrs.has('data-theme')).toBe(false)
    expect(() => applyTheme('light', null)).not.toThrow()
  })

  it('isDarkTheme follows the system only for system', () => {
    expect(isDarkTheme('system', true)).toBe(true)
    expect(isDarkTheme('system', false)).toBe(false)
    expect(isDarkTheme('light', true)).toBe(false)
    expect(isDarkTheme('dark', false)).toBe(true)
  })
})
