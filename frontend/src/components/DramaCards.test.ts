import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { DramaSummary } from '../api/types'
import { showFold } from '../pages/libraryForm'
import { DramaCards } from './DramaCards'

const drama = (over: Partial<DramaSummary>): DramaSummary =>
  ({
    id: 7, title_en: 'Moonlit', title_zh: '月光', status: 'translated', source_language: 'zh',
    media_type: 'audio_drama', custom_tags: ['fav'], ...over,
  }) as DramaSummary

const html = (items: DramaSummary[], selectedId: number | null = null) =>
  renderToStaticMarkup(createElement(DramaCards, { items, selectedId, onSelect: () => {} }))

describe('DramaCards', () => {
  it('links the title straight to the workspace and keeps a Details path', () => {
    const out = html([drama({})])
    expect(out).toContain('href="#/drama/7/source"')
    expect(out).toContain('>Moonlit</a>')
    expect(out).toContain('月光')
    expect(out).toContain('aria-label="Details: Moonlit"')
  })

  it('shows status badge and one type/lang line, no tags', () => {
    const out = html([drama({})])
    expect(out).toContain('<span class="badge">Translated</span>')
    expect(out).toContain('Audio drama · Chinese')
    expect(out).not.toContain('fav')
  })

  it('falls back to the id when a drama has no title, and marks the selection', () => {
    const out = html([drama({ title_en: null, title_zh: null })], 7)
    expect(out).toContain('>#7</a>')
    expect(out).toContain('class="selected"')
  })

  it('select mode: checkboxes instead of links and Details', () => {
    const out = renderToStaticMarkup(createElement(DramaCards, {
      items: [drama({}), drama({ id: 8, title_en: 'Other' })], selectedId: null, onSelect: () => {},
      selectMode: true, checked: new Set([7]), onToggle: () => {},
    }))
    expect(out).toContain('aria-label="Select Moonlit"')
    expect(out).toContain('aria-label="Select Moonlit" checked=""')
    expect(out).not.toContain('href=')
    expect(out).not.toContain('Details')
  })
})

describe('showFold', () => {
  it('hides empty sections but not loading, populated or failed ones', () => {
    expect(showFold(0, null)).toBe(false)
    expect(showFold(undefined, null)).toBe(true)
    expect(showFold(3, null)).toBe(true)
    expect(showFold(0, new Error('boom'))).toBe(true)
  })
})
