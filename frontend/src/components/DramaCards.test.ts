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
  it('links the title to the workspace, with Read and Details as quiet buttons', () => {
    const out = html([drama({})])
    expect(out).toContain('href="#/drama/7/source">Moonlit</a>')
    expect(out).toContain('月光')
    expect(out).toContain('aria-label="Details: Moonlit"')
    expect(out).toContain('href="#/read/7" aria-label="Read Moonlit" class="btn btn-ghost btn-sm drama-card-read"')
    expect(out).not.toContain('btn-primary')
  })

  it('shows humanized badges, the type and the tile character', () => {
    const out = html([drama({})])
    expect(out).toContain('<span class="pill pill-accent">Translated</span>')
    expect(out).toContain('<span class="pill pill-neutral">Chinese</span>')
    expect(out).toContain('Audio drama')
    expect(out).toContain('aria-hidden="true">月</div>')
    expect(out).not.toMatch(/audio_drama|>zh</)
  })

  it('shows two tags then +N', () => {
    const out = html([drama({ custom_tags: ['a', 'b', 'c', 'd'] })])
    expect(out).toContain('>a</span>')
    expect(out).toContain('>b</span>')
    expect(out).not.toContain('>c</span>')
    expect(out).toContain('>+2</span>')
  })

  it('falls back to the id when a drama has no title, and marks the selection', () => {
    const out = html([drama({ title_en: null, title_zh: null })], 7)
    expect(out).toContain('>#7</a>')
    expect(out).toContain('class="drama-card selected"')
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
