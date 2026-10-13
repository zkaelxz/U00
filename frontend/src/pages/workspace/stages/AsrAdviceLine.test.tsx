import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { AsrAdviceNote } from '../../../types/workspace'
import { AsrAdviceView } from './AsrAdviceLine'

const note = (over: Partial<AsrAdviceNote>): AsrAdviceNote => ({ id: 'x', severity: 'warn', text: 'Use Whisper.', fix: null, ...over })

describe('AsrAdviceView', () => {
  it('shows the note as one muted line with no button when the fix is not a setting change', () => {
    const html = renderToStaticMarkup(<AsrAdviceView note={note({})} onApply={() => undefined} />)
    expect(html).toContain('data-testid="transcribe-advisor"')
    expect(html).toContain('Use Whisper.')
    expect(html).not.toContain('<button')
  })

  it('offers Use recommended when the fix is a plain setting change', () => {
    const html = renderToStaticMarkup(<AsrAdviceView note={note({ fix: { asr_backend_choice: 'whisper' } })} onApply={() => undefined} />)
    expect(html).toContain('Use recommended')
    expect(html.match(/<button/g)).toHaveLength(1)
  })
})
