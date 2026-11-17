import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

import type { SeriesPageData } from '../../api/series'
import type { SeriesSharedGlossary } from '../../api/sharedGlossary'

const view: SeriesSharedGlossary = {
  series_id: 3,
  group: { id: 9, name: 'Creator', term_count: 1, series_count: 2, can_edit: true },
  terms: [{ id: -5, term_original: '老铁', term_translation: 'bro', notes: '', origin: 'shared', origin_name: 'Creator' }],
  can_edit: true,
}

// The card's two reads are effects, which a static render never runs: hand it the loaded data.
vi.mock('../../hooks/useLoad', async () => {
  const { listSharedGroups } = await import('../../api/sharedGlossary')
  return { useLoad: (load: unknown) => ({ data: load === listSharedGroups ? { items: [] } : view, error: null }) }
})

const { SharedGlossaryCard } = await import('./SharedGlossaryCard')

const page: SeriesPageData = {
  id: 3, name: 'Saga', is_private: false, owned_by_me: true, can_edit: true, has_instructions: false,
  glossary_term_count: 1, character_count: 0, speaker_mode: 'auto', titles: [],
}

describe('shared glossary terms on the series page', () => {
  it('removes a shared term only on a second press', () => {
    const html = renderToStaticMarkup(<SharedGlossaryCard data={page} onChanged={() => undefined} />)
    expect(html).toContain('老铁 → bro')
    expect(html).toContain('class="confirm-button"')
    expect(html).toContain('aria-label="Remove 老铁"')
    expect(html).toContain('>Remove…<')
  })
})
