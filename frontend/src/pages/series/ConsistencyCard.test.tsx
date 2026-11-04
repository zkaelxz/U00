import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { SeriesPageData } from '../../api/series'
import { ConsistencyCard } from './ConsistencyCard'
import { consistencySummaryText } from './seriesModel'

const page: SeriesPageData = {
  id: 3, name: 'Saga', is_private: false, owned_by_me: true, can_edit: true, has_instructions: false,
  glossary_term_count: 1, character_count: 0, speaker_mode: 'auto', titles: [],
}

describe('series consistency report', () => {
  it('states the counts in words', () => {
    const text = consistencySummaryText({
      titles_scanned: 2, lines_scanned: 6, terms_checked: 1, terms_found: 1,
      names_with_several_renderings: 1, flagged_lines: 2, glossary_missing_lines: 1,
    })
    expect(text).toContain('1 name has more than one rendering')
    expect(text).toContain('2 lines off-glossary')
    expect(text).toContain('1 line still flagged for a missing glossary name')
  })

  it('shows nothing and runs nothing until asked', () => {
    const html = renderToStaticMarkup(<ConsistencyCard data={page} />)
    expect(html).toContain('Check consistency')
    expect(html).not.toContain('series-consistency-report')
  })
})
