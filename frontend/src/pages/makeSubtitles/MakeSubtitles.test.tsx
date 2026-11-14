import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { FilePlan } from './MakeSubtitles'
import { useFileRun } from './useFileRun'

function Plan({ file = null }: { file?: File | null }) {
  const run = useFileRun({ runs: true })
  return <FilePlan run={{ ...run, file }} onChooseFile={() => undefined} onOnlyAdd={() => undefined} onClear={() => undefined} />
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

describe('a chosen file in the Add box', () => {
  const html = renderToStaticMarkup(<Plan file={new File(['abc'], 'My Show.mp3')} />)

  it('says what was found and offers Only add it and Clear side by side', () => {
    expect(html).toContain('an audio file, “My Show.mp3”, 3 B.')
    expect(html).toMatch(/>Only add it, don&#x27;t start<\/button><button type="button" class="[^"]*">Clear<\/button>/)
  })

  it('offers no Clear before a file is given', () => {
    expect(renderToStaticMarkup(<Plan />)).not.toContain('>Clear<')
  })
})
