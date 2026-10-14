import { describe, expect, it } from 'vitest'

import { applyDisplay, loadDisplay, parseTextSize, parseWidth, TEXT_KEY, WIDTH_KEY } from './display'

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
  })

  it('reads saved choices and survives storage that throws', () => {
    expect(loadDisplay(store({ [WIDTH_KEY]: 'full', [TEXT_KEY]: 'large' }))).toEqual({ width: 'full', text: 'large' })
    const broken = { getItem: () => { throw new Error('blocked') }, setItem: () => {}, removeItem: () => {} }
    expect(loadDisplay(broken)).toEqual({ width: 'standard', text: 'default' })
  })

  it('sets an attribute for a choice and removes it for the default', () => {
    const attrs = new Map<string, string>()
    const root = {
      setAttribute: (k: string, v: string) => attrs.set(k, v),
      removeAttribute: (k: string) => attrs.delete(k),
    } as unknown as HTMLElement
    applyDisplay({ width: 'wide', text: 'larger' }, root)
    expect(Object.fromEntries(attrs)).toEqual({ 'data-width': 'wide', 'data-text': 'larger' })
    applyDisplay({ width: 'standard', text: 'default' }, root)
    expect(attrs.size).toBe(0)
  })
})
