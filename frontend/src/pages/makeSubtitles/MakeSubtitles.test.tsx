import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { FilePlan } from './MakeSubtitles'
import { useFileRun } from './useFileRun'

function Plan() {
  const run = useFileRun({ runs: true })
  return <FilePlan run={run} onChooseFile={() => undefined} onOnlyAdd={() => undefined} />
}

// Server rendering stops before any effect runs, so this is the form as it first appears,
// before the translator list has answered.
describe('MakeSubtitles first paint', () => {
  const html = renderToStaticMarkup(<Plan />)

  it('shows a disabled loading option instead of a blank translator list', () => {
    expect(html).toMatch(/<select[^>]*disabled=""[^>]*>\s*<option value="" selected="">Loading translators\.\.\.<\/option>/)
  })

  it('spells out the English variants', () => {
    expect(html).toContain('English (UK)')
    expect(html).toContain('English (Australia)')
  })

  it('keeps the main button off and says a file is still needed, with Choose a file', () => {
    expect(html).toMatch(/<button type="submit"[^>]*disabled=""[^>]*>Make English subtitles<\/button>/)
    expect(html).toContain('Still needed: an audio or video file.')
    expect(html).toContain('>Choose a file</button>')
  })
})
