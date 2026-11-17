/** The Series folder list: every series the viewer can see, each opening its Series page. */
import { useMemo, useState } from 'react'

import { coverUrl } from '../../api/metadata'
import { listAllSeries } from '../../api/series'
import { Card } from '../../components/Card'
import { ErrorBanner } from '../../components/ErrorBanner'
import { PageShell } from '../../components/PageShell'
import { useLoad } from '../../hooks/useLoad'
import { routeHref } from '../../router'
import { filterSeries } from './seriesModel'
import './seriesPage.css'

export default function SeriesListPage() {
  const [query, setQuery] = useState('')
  const all = useLoad(listAllSeries, 0)
  const shown = useMemo(() => filterSeries(all.data?.items ?? [], query), [all.data, query])
  const total = all.data?.items.length ?? 0

  return (
    <PageShell
      width="standard"
      className="series-list-page"
      title="Series"
      back={(
        <nav aria-label="Breadcrumb" className="reader-crumbs">
          <a href={routeHref({ name: 'library' })}>Library</a>
          <span aria-hidden="true"> / </span>
          <span>Series</span>
        </nav>
      )}
      meta={all.data && (total === 1 ? '1 series' : `${total} series`)}
    >
      <ErrorBanner error={all.error} />
      {all.data && total === 0 && (
        <Card><p className="muted">No series yet. Add a title to a new series from its details.</p></Card>
      )}
      {total > 6 && (
        <input
          type="search" aria-label="Search series" placeholder="Search series" value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      )}
      {total > 0 && (
        <Card>
          <ul className="series-folders" data-testid="series-folders">
            {shown.map((s) => (
              <li key={s.id}>
                <a className="series-folder" href={routeHref({ name: 'series', id: s.id })}>
                  {s.cover_title_id ? (
                    <img className="series-folder-cover" src={coverUrl(s.cover_title_id)} alt="" loading="lazy" />
                  ) : (
                    <span className="series-folder-icon" aria-hidden="true">📁</span>
                  )}
                  <span className="series-folder-name">{s.name}</span>
                  <span className="muted series-folder-count">{s.title_count === 1 ? '1 title' : `${s.title_count} titles`}</span>
                </a>
              </li>
            ))}
          </ul>
          {shown.length === 0 && <p className="muted">No series match "{query}".</p>}
        </Card>
      )}
    </PageShell>
  )
}
