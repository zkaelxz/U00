/** The Series folder list: streamers first once any series has one, then series, each opening its Series page. */
import { useMemo, useState } from 'react'

import { coverUrl } from '../../api/metadata'
import { listAllSeries } from '../../api/series'
import { Card } from '../../components/Card'
import { ErrorBanner } from '../../components/ErrorBanner'
import { PageShell } from '../../components/PageShell'
import { useLoad } from '../../hooks/useLoad'
import { plural } from '../../labels'
import { routeHref } from '../../router'
import { filterSeries, filterStreamers, seriesOfStreamer, streamerFolders } from './seriesModel'
import './seriesPage.css'

// streamer: one streamer's series ("" for the series with none); undefined: the top level.
export default function SeriesListPage({ streamer }: { streamer?: string }) {
  const [query, setQuery] = useState('')
  const all = useLoad(listAllSeries, 0)
  const items = useMemo(() => all.data?.items ?? [], [all.data])
  const byStreamer = streamer === undefined && items.some((s) => s.streamer)
  const inFolder = useMemo(() => (streamer === undefined ? items : seriesOfStreamer(items, streamer)), [items, streamer])
  const folders = useMemo(() => (byStreamer ? filterStreamers(streamerFolders(items), query) : []), [byStreamer, items, query])
  const shown = useMemo(() => filterSeries(inFolder, query), [inFolder, query])
  const total = byStreamer ? streamerFolders(items).length : inFolder.length
  const here = streamer === undefined ? (byStreamer ? 'Streamers' : 'Series') : (streamer || 'No streamer')

  return (
    <PageShell
      width="standard"
      className="series-list-page"
      title={here}
      back={(
        <nav aria-label="Breadcrumb" className="reader-crumbs">
          <a href={routeHref({ name: 'library' })}>Library</a>
          {streamer !== undefined && (
            <>
              <span aria-hidden="true"> / </span>
              <a href={routeHref({ name: 'series-list' })}>Streamers</a>
            </>
          )}
          <span aria-hidden="true"> / </span>
          <span>{here}</span>
        </nav>
      )}
      meta={all.data && (byStreamer ? plural(total, 'streamer') : total === 1 ? '1 series' : `${total} series`)}
    >
      <ErrorBanner error={all.error} />
      {streamer === undefined && all.data && total === 0 && (
        <Card><p className="muted">No series yet. Add a title to a new series from its details.</p></Card>
      )}
      {total > 6 && (
        <input
          type="search" aria-label={byStreamer ? 'Search streamers' : 'Search series'}
          placeholder={byStreamer ? 'Search streamers' : 'Search series'} value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      )}
      {byStreamer && (
        <Card>
          <ul className="series-folders" data-testid="streamer-folders">
            {folders.map((f) => (
              <li key={f.name ?? ''}>
                <a className="series-folder" href={routeHref({ name: 'series-list', streamer: f.name ?? '' })}>
                  <span className="series-folder-icon" aria-hidden="true">{f.name === null ? '📂' : '🎙️'}</span>
                  <span className="series-folder-name">{f.name ?? 'No streamer'}</span>
                  <span className="muted series-folder-count">
                    {f.name === null ? plural(f.titleCount, 'title') : `${plural(f.seriesCount, 'series', 'series')} · ${plural(f.titleCount, 'title')}`}
                  </span>
                </a>
              </li>
            ))}
          </ul>
          {folders.length === 0 && <p className="muted">No streamers match "{query}".</p>}
          <p className="muted">A streamer is a name you give a series in its Settings tab.</p>
        </Card>
      )}
      {!byStreamer && total > 0 && (
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
      {streamer !== undefined && all.data && total === 0 && (
        <p className="muted">No series are filed under this streamer now.</p>
      )}
    </PageShell>
  )
}
