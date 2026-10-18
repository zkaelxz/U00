import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { MakeSubtitles } from './MakeSubtitles'

// Server rendering stops before any effect runs, so this is the form as it first appears,
// before the translator list has answered.
describe('MakeSubtitles first paint', () => {
  const html = renderToStaticMarkup(<MakeSubtitles />)

  it('shows a disabled loading option instead of a blank translator list', () => {
    expect(html).toMatch(/<select[^>]*disabled=""[^>]*>\s*<option value="" selected="">Loading translators\.\.\.<\/option>/)
  })

  it('says a title is added to the Library', () => {
    expect(html).toContain('This also adds a title to your Library.')
  })

  it('spells out the English variants', () => {
    expect(html).toContain('English (UK)')
    expect(html).toContain('English (Australia)')
  })
})
