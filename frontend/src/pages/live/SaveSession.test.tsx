import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { LiveCue } from '../../types/live'
import { SaveSession } from './SaveSession'

const cues: LiveCue[] = [{ id: 0, start: 1, end: 2, text: 'src', translated: 'en', translation: 'done' }]

describe('SaveSession', () => {
  it('offers both formats and the three contents, with both languages by default', () => {
    const html = renderToStaticMarkup(<SaveSession cues={cues} />)
    expect(html).toContain('Save as SRT')
    expect(html).toContain('Save as text')
    expect(html).toContain('Translation only')
    expect(html).toContain('Original only')
    expect(html).toMatch(/<option value="both"[^>]*selected|value="both"/)
  })
})
