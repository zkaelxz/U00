/*
 * Sources page (#/sources), docs spec ux-sources-diagnostics §1.
 *
 * Search the enabled sources by title (a paced background job), open a
 * series to see its chapter list (another job, per source), follow tracked
 * series' new chapters, and (PC only) the per-source switches, pacing and
 * cache. Cover images are never rendered: loading them would bypass pacing.
 * No Import and no Track: those have no API yet.
 *
 * Desktop (>=1024 px): search and results left, the open series right
 * (sticky), New chapters and Source settings below both. Narrower: one
 * column, and an open series replaces the results until "‹ Results".
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { listNotifications, listSources, listTracked, SEARCH_JOB_ID, seriesJobId, startSeries, untrackSeries } from '../api/sources'
import { ErrorBanner } from '../components/ErrorBanner'
import { useMediaQuery } from '../hooks/useMediaQuery'
import { usePcOnly } from '../hooks/usePcOnly'
import { usePersistedState } from '../hooks/usePersistedState'
import type {
  OpenSeries,
  SearchResult,
  SeriesResult,
  SourceHealth,
  SourceNotification,
  SourceSummary,
  TrackedSeries,
} from '../types/sources'
import { NewChapters } from './sources/NewChapters'
import { SearchPanel } from './sources/SearchPanel'
import { SeriesPanel } from './sources/SeriesPanel'
import { SourceSettings } from './sources/SourceSettings'
import { SEARCH_REMOTE_ALLOWED, pageSummary } from './sources/sourcesFormat'
import { useSourcesJob } from './sources/useSourcesJob'
import './sources/sources.css'

function validSeries(v: unknown): OpenSeries | null {
  const o = v as Partial<OpenSeries> | null
  return o && typeof o.source === 'string' && typeof o.series_id === 'string'
    ? { source: o.source, series_id: o.series_id, title: typeof o.title === 'string' ? o.title : '' }
    : null
}

export default function SourcesPage() {
  const pc = usePcOnly()
  const remote = pc === 'remote'
  const wide = useMediaQuery('(min-width: 1024px)')
  const phone = useMediaQuery('(max-width: 640px)')

  const [sources, setSources] = useState<SourceSummary[] | null>(null)
  const [tracked, setTracked] = useState<TrackedSeries[]>([])
  const [notifications, setNotifications] = useState<SourceNotification[]>([])
  const [loadError, setLoadError] = useState<unknown>(null)
  const [untrackBusy, setUntrackBusy] = useState<string | null>(null)
  const [untrackError, setUntrackError] = useState<unknown>(null)

  const [storedSeries, setStoredSeries] = usePersistedState<OpenSeries | null>('sources.lastSeries', null)
  const open = validSeries(storedSeries)
  const [cleared, setCleared] = useState(false)
  const [focusKey, setFocusKey] = useState(0)
  const opener = useRef<{ key: string; scrollY: number } | null>(null)

  const search = useSourcesJob<SearchResult>(SEARCH_JOB_ID)
  const series = useSourcesJob<SeriesResult>(open ? seriesJobId(open.source) : null)

  useEffect(() => {
    listSources().then(setSources, setLoadError)
    // Together, so New chapters mounts once with both (it opens when there is news).
    Promise.all([listTracked().catch(() => []), listNotifications().catch(() => [])]).then(([t, n]) => {
      setTracked(t)
      setNotifications(n)
    })
  }, [])

  const display = useCallback(
    (name: string) => sources?.find((s) => s.name === name)?.display_name ?? name,
    [sources],
  )

  function openSeries(s: OpenSeries, openerKey?: string) {
    opener.current = openerKey ? { key: openerKey, scrollY: window.scrollY } : null
    setStoredSeries(s)
    setCleared(false)
    setFocusKey((k) => k + 1)
    series.start(() => startSeries(s.source, s.series_id), seriesJobId(s.source))
  }

  function reloadSeries() {
    if (!open) return
    setCleared(false)
    series.start(() => startSeries(open.source, open.series_id), seriesJobId(open.source))
  }

  function closeSeries() {
    setStoredSeries(null)
    setCleared(false)
    const o = opener.current
    opener.current = null
    // After the results are shown again: same scroll position, focus on the opener.
    requestAnimationFrame(() => {
      if (!o) return
      window.scrollTo(0, o.scrollY)
      document.querySelector<HTMLElement>(`[data-opener="${CSS.escape(o.key)}"]`)?.focus({ preventScroll: true })
    })
  }

  async function untrack(t: TrackedSeries) {
    const key = `${t.source}:${t.series_id}`
    setUntrackError(null)
    setUntrackBusy(key)
    try {
      setTracked(await untrackSeries(t.source, t.series_id))
    } catch (e) {
      setUntrackError(e)
    } finally {
      setUntrackBusy(null)
    }
  }

  const replaceSource = (s: SourceSummary) =>
    setSources((cur) => (cur ? cur.map((x) => (x.name === s.name ? s : x)) : cur))
  const setHealth = (name: string, h: SourceHealth) =>
    setSources((cur) => (cur ? cur.map((x) => (x.name === name ? { ...x, health: h.light } : x)) : cur))
  const adultChanged = (name: string) => {
    if (open?.source === name) {
      series.reset()
      setCleared(true)
    }
  }

  const seriesShown = !!open && (series.status !== 'idle' || cleared || !!series.startError)
  const openTracked = open
    ? tracked.find((t) => t.source === open.source && t.series_id === open.series_id) ?? null
    : null
  const summary = sources ? pageSummary(sources) : null
  const searchBlocked = remote && !SEARCH_REMOTE_ALLOWED

  return (
    <div className={`sources-page${wide ? ' wide' : ''}${seriesShown ? ' has-series' : ''}`}>
      <section className="panel sources-main" aria-label="Sources">
        <h2>Sources</h2>
        <p className="muted sources-summary" data-testid="sources-summary">
          {summary ? (
            <>
              {summary.on}
              {summary.paused && <span className="warn"> · {summary.paused}</span>}
            </>
          ) : loadError ? null : (
            'Loading…'
          )}
        </p>
        <ErrorBanner error={loadError} />
        {searchBlocked ? (
          <p className="muted">Searching sources is PC only for now.</p>
        ) : (
          sources && (
            <SearchPanel
              sources={sources}
              remote={remote}
              job={search}
              resultsHidden={seriesShown && !wide}
              onOpen={openSeries}
            />
          )
        )}
      </section>

      {seriesShown && open && (
        <SeriesPanel
          open={open}
          display={display(open.source)}
          job={series}
          tracked={openTracked}
          remote={remote}
          cleared={cleared}
          focusKey={focusKey}
          showBack={!wide}
          onReload={reloadSeries}
          onClose={closeSeries}
          onUntrack={untrack}
          untrackBusy={!!openTracked && untrackBusy === `${openTracked.source}:${openTracked.series_id}`}
        />
      )}

      {(notifications.length > 0 || tracked.length > 0) && (
        <section className="panel sources-wide" aria-label="New chapters">
          <NewChapters
            notifications={notifications}
            tracked={tracked}
            display={display}
            onOpen={(s) => openSeries(s)}
            onDismissed={(id) => setNotifications((ns) => ns.filter((n) => n.id !== id))}
            onUntrack={untrack}
            untrackBusy={untrackBusy}
            untrackError={untrackError}
            clearUntrackError={() => setUntrackError(null)}
          />
        </section>
      )}

      {sources && (
        <section className="panel sources-wide" aria-label="Source settings">
          <SourceSettings
            pc={pc}
            phone={phone}
            sources={sources}
            onSource={replaceSource}
            onHealth={setHealth}
            onAdultChanged={adultChanged}
          />
        </section>
      )}
    </div>
  )
}
