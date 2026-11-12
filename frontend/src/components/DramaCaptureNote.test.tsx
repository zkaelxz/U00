import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { CaptureNoteView } from './DramaCaptureNote'

const note = (summary: string) => ({ summary, created_at: '2026-11-12T17:42:00Z' })

describe('CaptureNoteView', () => {
  it('shows nothing for a title that was never captured', () => {
    expect(renderToStaticMarkup(<CaptureNoteView notes={[]} />)).toBe('')
  })

  it('shows the newest line and offers the earlier ones', () => {
    const html = renderToStaticMarkup(<CaptureNoteView notes={[note('Saved 69 of 69 pages.'), note('Saved 58 of 58 pages.')]} />)
    expect(html).toContain('Last capture')
    expect(html).toContain('Saved 69 of 69 pages.')
    expect(html).toContain('Earlier captures (1)')
    expect(html).not.toContain('Saved 58 of 58 pages.')
  })
})
