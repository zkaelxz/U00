/** A comic's Media tab: its pages and chapters, and the way into Scanlate where they are translated. */
import { useEffect, useState } from 'react'

import { comicApi } from '../../../api/comic'
import { ButtonLink } from '../../../components/Button'
import { routeHref } from '../../../router'
import { pagesSummary, type PageCounts as Counts } from '../comicStage'

export function ComicPagesPanel({ dramaId, reloadKey = 0 }: { dramaId: number; reloadKey?: number }) {
  const [counts, setCounts] = useState<Counts | null>(null)
  useEffect(() => {
    let cancelled = false
    comicApi.pages(dramaId).then(
      (r) => !cancelled && setCounts({ pages: r.page_count, chapters: r.chapters?.filter((c) => c.known).length ?? 0 }),
      () => undefined,
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, reloadKey])

  const summary = pagesSummary(counts)
  return (
    <section className="panel" aria-label="Comic pages">
      <h2 className="source-subhead">Pages</h2>
      <p className="muted" data-testid="comic-page-summary">{summary}</p>
      <p className="muted">
        Pages are added and translated in Scanlate, or grabbed with the phone app or the browser extension. The chapter list is in Scanlate, in the ⋯ Chapters & pages menu, and under Chapters on the title's Guided page.
      </p>
      <div className="actions">
        <ButtonLink variant="primary" href={routeHref({ name: 'comic', id: dramaId, page: null })}>Open in Scanlate</ButtonLink>
      </div>
    </section>
  )
}
