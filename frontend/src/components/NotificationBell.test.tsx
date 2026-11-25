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
      [{ page: 'glossary', drama_id: 5, series_id: null, follow_id: null }, '#/drama/5/translate?tab=glossary', 'Open the Glossary tab of this title'],
      [{ page: 'review', drama_id: 5, series_id: null, follow_id: null }, '#/drama/5/review', 'Open the Review step of this title'],
      [{ page: 'series', drama_id: null, series_id: 3, follow_id: null }, '#/series/3', 'Open the series page'],
      [{ page: 'jobs', drama_id: null, series_id: null, follow_id: null }, '#/jobs', 'Open the Jobs page'],
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
    expect(row({ target: { page: 'glossary', drama_id: null, series_id: null, follow_id: null } })).not.toContain('<a')
    expect(row({ target: { page: 'nowhere', drama_id: 1 } as unknown as NotificationItem['target'] })).not.toContain('<a')
  })

  it('links a finished entry to the title it finished', () => {
    expect(row({ kind: 'job_done', text: 'Finished: Translation', target: { page: 'watch', drama_id: 7, series_id: null, follow_id: null } })).toContain('href="#/watch/7"')
    expect(row({ kind: 'job_done', text: 'Finished: Translation', target: { page: 'title', drama_id: 7, series_id: null, follow_id: null } })).toContain('href="#/drama/7"')
  })

  it('does not link a failed entry', () => {
    expect(row({ kind: 'job_failed', text: 'Failed: Translation', target: { page: 'watch', drama_id: 7, series_id: null, follow_id: null } })).not.toContain('<a')
  })
})
