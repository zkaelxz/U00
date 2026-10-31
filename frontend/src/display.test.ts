import { describe, expect, it } from 'vitest'

/// <reference types="node" />
import { readFileSync } from 'node:fs'

import { applyDisplay, DENSITY_KEY, loadDisplay, parseDensity, parseStyle, parseTextSize, parseWidth, STYLE_KEY, TEXT_KEY, WIDTH_KEY } from './display'
import { SETTINGS_INDEX } from './pages/settings/settingsIndex'

const css = readFileSync(new URL('./index.css', import.meta.url), 'utf8')

const store = (items: Record<string, string>) => ({
  getItem: (k: string) => items[k] ?? null,
  setItem: () => {},
  removeItem: () => {},
})

describe('display preferences', () => {
  it('falls back to the defaults for anything it does not know', () => {
    expect(parseWidth('huge')).toBe('standard')
    expect(parseWidth(null)).toBe('standard')
    expect(parseTextSize('tiny')).toBe('default')
    expect(parseTextSize(undefined)).toBe('default')
    expect(parseDensity('tight')).toBe('comfortable')
    expect(parseDensity(null)).toBe('comfortable')
  })

  it('reads saved choices and survives storage that throws', () => {
    expect(loadDisplay(store({ [WIDTH_KEY]: 'full', [TEXT_KEY]: 'large', [DENSITY_KEY]: 'compact', [STYLE_KEY]: 'soft' }))).toEqual({ width: 'full', text: 'large', density: 'compact', style: 'soft' })
    const broken = { getItem: () => { throw new Error('blocked') }, setItem: () => {}, removeItem: () => {} }
    expect(loadDisplay(broken)).toEqual({ width: 'standard', text: 'default', density: 'comfortable', style: 'frost' })
  })

  it('sets an attribute for a choice and removes it for the default', () => {
    const attrs = new Map<string, string>()
    const root = {
      setAttribute: (k: string, v: string) => attrs.set(k, v),
      removeAttribute: (k: string) => attrs.delete(k),
    } as unknown as HTMLElement
    applyDisplay({ width: 'wide', text: 'larger', density: 'compact', style: 'soft' }, root)
    expect(Object.fromEntries(attrs)).toEqual({ 'data-width': 'wide', 'data-text': 'larger', 'data-density': 'compact', 'data-style': 'soft' })
    applyDisplay({ width: 'standard', text: 'default', density: 'comfortable', style: 'frost' }, root)
    expect(attrs.size).toBe(0)
  })

  it('tightens the spacing tokens only for compact on a fine pointer, and keeps the touch heights literal', () => {
    const block = css.match(/@media \(pointer: fine\) and \(min-width: 641px\) \{\s*html\[data-density='compact'\] \{([^}]*)\}/)?.[1] ?? ''
    for (const t of ['--space-1', '--space-2', '--space-3', '--space-4', '--control-h']) expect(block).toContain(t)
    const touch = css.match(/@media \(pointer: coarse\), \(max-width: 640px\) \{\s*button:not[^}]*min-height: 44px; \}/)?.[0] ?? ''
    expect(touch).toContain('min-height: 44px')
    expect(touch).not.toContain('--control-h')
  })

  it('falls back to Frost for an unknown saved style', () => {
    expect(parseStyle('neon')).toBe('frost')
    expect(parseStyle('soft')).toBe('soft')
  })

  it('is found by the settings search', () => {
    const entry = SETTINGS_INDEX.find((x) => x.cardId === 'display')
    expect(entry?.keywords).toContain('density compact comfortable spacing tight')
  })
})
