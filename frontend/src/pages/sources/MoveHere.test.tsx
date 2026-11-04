import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { TrackedSeries } from '../../types/sources'
import { MoveHere } from './MoveHere'

const t: TrackedSeries = {
  source: 'old', series_id: 'o1', title: 'Old title', url: '', drama_id: 3, last_checked: null,
  last_check_error: null, save_cbz: false, extension_only: false,
}
const show = (candidates: { tracked: TrackedSeries; label: string }[]) =>
  renderToStaticMarkup(
    <MoveHere source="new" seriesId="n1" title="New" candidates={candidates} onMoved={() => {}} />,
  )

describe('MoveHere', () => {
  it('offers each tracked title and no move until one is picked and compared', () => {
    const html = show([{ tracked: t, label: 'Old title · Old' }])
    expect(html).toContain('Move a tracked title here')
    expect(html).toContain('Old title · Old')
    expect(html).not.toContain('Move here…')
  })

  it('shows nothing when no tracked title could move', () => {
    expect(show([])).toBe('')
  })
})
