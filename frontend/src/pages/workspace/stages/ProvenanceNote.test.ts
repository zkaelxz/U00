import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { ProvenanceRow } from '../../../types/research'
import { provenanceNotes } from '../researchForm'
import { ProvenanceNote, ProvenanceNotes } from './ProvenanceNote'

const row = (over: Partial<ProvenanceRow>): ProvenanceRow => ({
  id: 1, field: 'title_en', value: 'v', sources: [], status: 'applied', ...over,
})

describe('provenanceNotes', () => {
  it('names source titles and the date, never URLs', () => {
    const [n] = provenanceNotes([row({
      source_url: 'https://secret.example/x', retrieved_at: '2026-09-29T10:00:00Z',
      sources: [{ title: 'Example', url: 'https://secret.example/x' }, { title: 'Example', url: 'https://secret.example/y' }],
    })])
    expect(n.label).toBe('English title')
    expect(n.text).toMatch(/^Filled from Example on /)
    expect(n.text).not.toContain('http')
  })

  it('keeps the newest row per field and words each status', () => {
    const notes = provenanceNotes([
      row({ id: 1, status: 'applied' }),
      row({ id: 3, status: 'alternate' }),
      row({ id: 2, field: 'studio', status: 'verified' }),
    ])
    expect(notes.map((n) => n.text)).toEqual(['Confirmed', 'Saved beside the existing value'])
  })

  it('is empty when nothing is stored', () => {
    expect(provenanceNotes([])).toEqual([])
  })
})

describe('ProvenanceNotes', () => {
  it('renders nothing without notes', () => {
    expect(renderToStaticMarkup(createElement(ProvenanceNotes, { notes: [] }))).toBe('')
  })

  it('renders a labelled list in a disclosure', () => {
    const html = renderToStaticMarkup(createElement(ProvenanceNotes, {
      notes: [{ field: 'studio', label: 'Studio', text: 'Filled from Example' }],
    }))
    expect(html).toContain('<summary>Where saved details came from (1)</summary>')
    expect(html).toContain('aria-label="Saved sources"')
    expect(html).toContain('<strong>Studio:</strong> Filled from Example')
  })

  it('renders nothing while loading', () => {
    expect(renderToStaticMarkup(createElement(ProvenanceNote, { dramaId: 1 }))).toBe('')
  })
})
