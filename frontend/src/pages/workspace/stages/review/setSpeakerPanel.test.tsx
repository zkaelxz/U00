import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { SELECTION_ACTIONS } from './selectionActions'
import { SetSpeakerPanel } from './SetSpeakerPanel'

describe('Set speaker', () => {
  it('is offered in the selection bar', () => {
    expect(SELECTION_ACTIONS.map((a) => a.label)).toContain('Set speaker…')
  })

  it('names the line count and keeps Set speaker disabled until a speaker is chosen', () => {
    const html = renderToStaticMarkup(<SetSpeakerPanel dramaId={1} lineIds={[4]} onChanged={() => {}} onClose={() => {}} />)
    expect(html).toContain('Speaker for 1 line')
    expect(renderToStaticMarkup(<SetSpeakerPanel dramaId={1} lineIds={[4, 5]} onChanged={() => {}} onClose={() => {}} />)).toContain('Speaker for 2 lines')
    expect(html).toMatch(/<button[^>]*disabled[^>]*>Set speaker<\/button>/)
  })
})
