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

  it('has the size classes and no unused field-wide opt-out', () => {
    for (const size of ['xs', 'sm', 'md']) expect(css).toContain(`.field-control > .w-${size} { max-width: var(--field-w-${size}); }`)
    expect(css).not.toContain('.field-wide')
  })

  it('lets every control fill the row on a phone', () => {
    const phone = css.match(/@media \(max-width: 640px\) \{\s*\.field-control > select[^}]*\{ max-width: none; \}/)
    expect(phone?.[0]).toMatch(/\.w-xs[\s\S]*\.w-sm[\s\S]*\.w-md/)
  })

  it('has the toggle grid helper and no unused field grid', () => {
    expect(css).not.toContain('.field-grid')
    // 15rem columns, never wider than the screen.
    expect(css).toMatch(/\.toggle-grid \{[^}]*minmax\(min\(100%, 15rem\), 1fr\)/)
  })
})

describe('Library list columns', () => {
  it('keeps short columns on one line and lets only the Title column wrap, between words', () => {
    expect(css).toMatch(/\.drama-table th, \.drama-table td \{[^}]*overflow-wrap: normal;[^}]*white-space: nowrap;/)
    expect(css).toMatch(/\.drama-table \.title-col \{[^}]*white-space: normal;[^}]*min-width: 12rem;/)
  })
})
