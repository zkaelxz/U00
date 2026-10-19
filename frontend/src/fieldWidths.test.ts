/// <reference types="node" />
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const css = readFileSync(new URL('./index.css', import.meta.url), 'utf8')

describe('field width tokens', () => {
  it('defines the size tokens and the measure', () => {
    for (const t of ['--field-w-xs: 6rem', '--field-w-sm: 11rem', '--field-w-md: 18rem', '--field-w-lg: 28rem', '--measure: 70ch']) {
      expect(css).toContain(t)
    }
  })

  it('caps a select and a text input at the large token and a textarea at 56rem', () => {
    expect(css).toMatch(/\.field-control > select,\s*\.field-control > input:not\([^)]*\) \{ max-width: var\(--field-w-lg\); \}/)
    expect(css).toMatch(/\.field-control > textarea \{ max-width: 56rem; \}/)
  })

  it('has the size classes', () => {
    for (const size of ['xs', 'sm', 'md']) expect(css).toContain(`.field-control > .w-${size} { max-width: var(--field-w-${size}); }`)
  })

  it('lets every control fill the row on a phone', () => {
    const phone = css.match(/@media \(max-width: 640px\) \{\s*\.field-control > select[^}]*\{ max-width: none; \}/)
    expect(phone?.[0]).toMatch(/\.w-xs[\s\S]*\.w-sm[\s\S]*\.w-md/)
  })

  it('has the toggle grid helper', () => {
    expect(css).toMatch(/\.toggle-grid \{[^}]*minmax\(15rem, 1fr\)/)
  })
})
