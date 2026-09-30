import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  applyTheme,
  getThemePref,
  loadTheme,
  parseTheme,
  resetThemeState,
  resolveTheme,
  saveTheme,
  setThemePref,
  THEME_KEY,
  THEME_OPTIONS,
  themeLabel,
  type ThemeStorage,
} from './theme'

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

function fakeRoot() {
  const attrs = new Map<string, string>()
  const root = {
    setAttribute: (k: string, v: string) => void attrs.set(k, v),
    removeAttribute: (k: string) => void attrs.delete(k),
  } as unknown as HTMLElement
  return { attrs, root }
}

describe('theme preference', () => {
  it('parses light, dark and sepia; anything else is system', () => {
    expect(parseTheme('dark')).toBe('dark')
    expect(parseTheme('light')).toBe('light')
    expect(parseTheme('sepia')).toBe('sepia')
    expect(parseTheme('neon')).toBe('system')
    expect(parseTheme(null)).toBe('system')
  })

  it('saves and loads; system removes the key', () => {
    const s = memory()
    saveTheme('dark', s)
    expect(s.data.get(THEME_KEY)).toBe('dark')
    expect(loadTheme(s)).toBe('dark')
    saveTheme('sepia', s)
    expect(loadTheme(s)).toBe('sepia')
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
    const { attrs, root } = fakeRoot()
    applyTheme('dark', root)
    expect(attrs.get('data-theme')).toBe('dark')
    applyTheme('sepia', root)
    expect(attrs.get('data-theme')).toBe('sepia')
    applyTheme('system', root)
    expect(attrs.has('data-theme')).toBe(false)
    expect(() => applyTheme('light', null)).not.toThrow()
  })

  it('resolveTheme follows the system only for system', () => {
    expect(resolveTheme('system', true)).toBe('dark')
    expect(resolveTheme('system', false)).toBe('light')
    expect(resolveTheme('light', true)).toBe('light')
    expect(resolveTheme('dark', false)).toBe('dark')
    expect(resolveTheme('sepia', true)).toBe('sepia')
  })

  it('lists the four choices with plain labels', () => {
    expect(THEME_OPTIONS.map((o) => o.label)).toEqual(['Match this device', 'Light', 'Dark', 'Sepia'])
    expect(themeLabel('sepia')).toBe('Sepia')
  })
})

describe('shared theme state', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    resetThemeState()
  })

  it('setThemePref saves, applies and is what getThemePref returns', () => {
    const s = memory()
    const { attrs, root } = fakeRoot()
    vi.stubGlobal('window', { localStorage: s })
    vi.stubGlobal('document', { documentElement: root })
    resetThemeState()
    expect(getThemePref()).toBe('system')
    setThemePref('sepia')
    expect(getThemePref()).toBe('sepia')
    expect(s.data.get(THEME_KEY)).toBe('sepia')
    expect(attrs.get('data-theme')).toBe('sepia')
    setThemePref('system')
    expect(getThemePref()).toBe('system')
    expect(s.data.has(THEME_KEY)).toBe(false)
    expect(attrs.has('data-theme')).toBe(false)
  })

  it('reads the saved choice on first use (a reload)', () => {
    const s = memory()
    s.setItem(THEME_KEY, 'dark')
    vi.stubGlobal('window', { localStorage: s })
    resetThemeState()
    expect(getThemePref()).toBe('dark')
  })
})
