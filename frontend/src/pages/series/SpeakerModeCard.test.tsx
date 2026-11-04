import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { SeriesPageData } from '../../api/series'
import { SpeakerModeCard } from './SpeakerModeCard'

const page = (over: Partial<SeriesPageData>): SeriesPageData => ({
  id: 3, name: 'Saga', is_private: false, owned_by_me: true, can_edit: true, has_instructions: false,
  glossary_term_count: 0, character_count: 0, speaker_mode: 'auto', titles: [], ...over,
})

describe('series speaker setting', () => {
  it('shows the stored mode as selected and is editable for an editor', () => {
    const html = renderToStaticMarkup(<SpeakerModeCard data={page({ speaker_mode: 'solo' })} />)
    expect(html).toMatch(/<option value="solo" selected/)
    expect(html).not.toContain('disabled')
  })

  it('is read-only for a viewer', () => {
    const html = renderToStaticMarkup(<SpeakerModeCard data={page({ can_edit: false })} />)
    expect(html).toContain('disabled')
    expect(html).toContain('You can view this but not change it.')
  })
})
