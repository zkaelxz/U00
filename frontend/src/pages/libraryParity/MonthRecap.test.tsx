import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { LibraryRecap } from '../../types/library'
import { MonthRecapView } from './MonthRecap'

const RECAP: LibraryRecap = {
  month: '2026-10', titles_read: 3, titles_finished: 1, chapters_read: 0, moments_saved: 2, active_days: 9,
  most_days: { drama_id: 4, title: 'Example Story', days: 6 }, spend_usd: null,
}
const html = (r: LibraryRecap) => renderToStaticMarkup(<MonthRecapView recap={r} monthName="October" onDismiss={() => {}} />)

describe('MonthRecapView', () => {
  it('shows the month, its numbers and the title with the most days', () => {
    const out = html(RECAP)
    expect(out).toContain('Your October')
    expect(out).toContain('titles read')
    expect(out).toContain('moments saved')
    expect(out).not.toContain('chapters read')
    expect(out).toContain('Example Story')
    expect(out).toContain('aria-label="Dismiss"')
    expect(out).not.toContain('spent')
  })

  it('names no title when none was opened on more than one day', () => {
    expect(html({ ...RECAP, most_days: { drama_id: 4, title: 'Example Story', days: 1 } })).not.toContain('Example Story')
  })

  it('renders nothing for an empty month', () => {
    expect(html({ ...RECAP, titles_read: 0, titles_finished: 0, moments_saved: 0, active_days: 0, most_days: null })).toBe('')
  })
})
