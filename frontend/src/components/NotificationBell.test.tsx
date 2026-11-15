import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { NotificationItem } from '../types/notifications'
import { NotificationRow } from './NotificationBell'

const row = (over: Partial<NotificationItem>) =>
  renderToStaticMarkup(
    <ul>
      <NotificationRow
        isNew={false}
        item={{ id: 1, at: Date.now() / 1000, kind: 'waiting', text: 'Waiting for you: 3 glossary names to review - Signal', action: null, follow_id: null, starts_at: null, target: null, ...over }}
      />
    </ul>,
  )

describe('NotificationRow', () => {
  it('shows a waiting entry with an Open link for each target', () => {
    const cases = [
      [{ page: 'glossary', drama_id: 5, series_id: null }, '#/drama/5/translate?tab=glossary', 'Open the Glossary tab of this title'],
      [{ page: 'review', drama_id: 5, series_id: null }, '#/drama/5/review', 'Open the Review step of this title'],
      [{ page: 'series', drama_id: null, series_id: 3 }, '#/series/3', 'Open the series page'],
      [{ page: 'jobs', drama_id: null, series_id: null }, '#/jobs', 'Open the Jobs page'],
    ] as const
    for (const [target, href, title] of cases) {
      const html = row({ target })
      expect(html).toContain('Waiting')
      expect(html).toContain('3 glossary names to review - Signal')
      expect(html).toContain(`href="${href}"`)
      expect(html).toContain(`title="${title}"`)
      expect(html).toContain('>Open</a>')
    }
  })

  it('shows no link for a waiting entry with no target, or one the page does not know', () => {
    expect(row({ target: null })).not.toContain('<a')
    expect(row({ target: { page: 'glossary', drama_id: null, series_id: null } })).not.toContain('<a')
    expect(row({ target: { page: 'nowhere', drama_id: 1 } as unknown as NotificationItem['target'] })).not.toContain('<a')
  })

  it('never links a finished entry, even with a target', () => {
    expect(row({ kind: 'job_done', text: 'Finished: Translation', target: { page: 'jobs', drama_id: null, series_id: null } })).not.toContain('<a')
  })
})
