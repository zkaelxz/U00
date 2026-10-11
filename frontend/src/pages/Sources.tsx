/*
 * Sources page (#/sources).
 *
 * One "Search or paste a link" card with a two-way switch
 * (FindModeSwitch): search the enabled sources by title (a paced
 * background job) or paste a link (UrlBox: preview it, open its series or
 * import a novel page); the other mode stays mounted, hidden, so its text
 * and results survive a switch. Open a series to see its chapter list
 * (another job, per source), tick chapters to import them into a drama or
 * track the series for new chapters (SeriesPanel). New chapters: Check
 * now, the notifications, and the tracked series (stop tracking, which
 * drama auto-import goes to). Cover images are never rendered: loading
 * them would bypass pacing.
 *
 * Source settings (PC only): per-source switches, health, details with
 * sign-in (a window on the PC) and per-tier "Test now", pacing and cache,
 * the proxy, site profiles. Searching, importing, tracking and Check now
 * are PC only for now too (SEARCH_REMOTE_ALLOWED / IMPORT_REMOTE_ALLOWED).
 *
 * Desktop (>=1024 px): search and results left, the open series right
 * (sticky), New chapters and Source settings below both. Narrower: one
 * column, and an open series replaces the results until "‹ Results".
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { listNotifications, listSources, listTracked, SEARCH_JOB_ID, seriesJobId, startSeries, untrackSeries } from '../api/sources'
import { Badge } from '../components/Badge'
import { Card } from '../components/Card'
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
import { FindModeSwitch, type FindMode } from './sources/FindModeSwitch'
import { NewChapters } from './sources/NewChapters'
import { SearchPanel } from './sources/SearchPanel'
import { SeriesPanel } from './sources/SeriesPanel'
import { SourceSettings } from './sources/SourceSettings'
import { UrlBox } from './sources/UrlBox'
import { IMPORT_REMOTE_ALLOWED } from './sources/urlImportFormat'
import { SEARCH_REMOTE_ALLOWED, pageSummary } from './sources/sourcesFormat'
import { seriesView } from './sources/sourcesSeries'
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

  const [storedMode, setStoredMode] = usePersistedState<FindMode>('sources.find', 'search')
  const [storedSeries, setStoredSeries] = usePersistedState<OpenSeries | null>('sources.lastSeries', null)
  const open = validSeries(storedSeries)
  const [cleared, setCleared] = useState(false)
  const [focusKey, setFocusKey] = useState(0)
  const opener = useRef<{ key: string; scrollY: number } | null>(null)
  // A chapter to tick in the series opened from a pasted chapter link.
  const [preselect, setPreselect] = useState<{ source: string; series_id: string; chapter_id: string } | null>(null)
  // A web-search result handed to the Paste a link box (n: a new hand-off each time).
  const [linkHandoff, setLinkHandoff] = useState<{ url: string; n: number } | null>(null)

  const search = useSourcesJob<SearchResult>(SEARCH_JOB_ID)
  const seriesId = open ? seriesJobId(open.source) : null
  // No 409 reattach: the id is per source, so the running run may be another series.
  const series = useSourcesJob<SeriesResult>(seriesId, { reattachOn409: false })
  const view = seriesView(open, series, seriesId)
  // Another series was loading; once it ends, keep the panel (with Try
  // again) until the user acts, instead of letting it vanish.
  const [wasBusy, setWasBusy] = useState(false)
  if (view.busyOther && !wasBusy) setWasBusy(true)

  // Together, so New chapters mounts once with both (it opens when there is news).
  const loadTracking = useCallback(() => {
    Promise.all([listTracked().catch(() => null), listNotifications().catch(() => null)]).then(([t, n]) => {
      if (t) setTracked(t)
      if (n) setNotifications(n)
    })
  }, [])

  useEffect(() => {
    listSources().then(setSources, setLoadError)
    loadTracking()
  }, [loadTracking])

  const display = useCallback(
    (name: string) => sources?.find((s) => s.name === name)?.display_name ?? name,
    [sources],
  )

  // Opens from a list: no chapter is ticked.
  function openSeries(s: OpenSeries, openerKey?: string) {
    setPreselect(null)
    showSeries(s, openerKey)
  }

  function showSeries(s: OpenSeries, openerKey?: string) {
    opener.current = openerKey ? { key: openerKey, scrollY: window.scrollY } : null
    // Already open and loading (or its start is in flight): just go to it.
    if (open && open.source === s.source && open.series_id === s.series_id && view.status === 'running') {
      setFocusKey((k) => k + 1)
      return
    }
    setStoredSeries(s)
    setCleared(false)
    setWasBusy(false)
    setFocusKey((k) => k + 1)
    series.start(() => startSeries(s.source, s.series_id), seriesJobId(s.source))
  }

  function openFromUrl(s: OpenSeries, chapterId: string | null) {
    setPreselect(chapterId ? { source: s.source, series_id: s.series_id, chapter_id: chapterId } : null)
    showSeries(s)
  }

  function reloadSeries() {
    if (!open) return
    setCleared(false)
    setWasBusy(false)
    series.start(() => startSeries(open.source, open.series_id), seriesJobId(open.source))
  }

  function closeSeries() {
    setPreselect(null)
    setStoredSeries(null)
    setCleared(false)
    setWasBusy(false)
    const o = opener.current
    opener.current = null
    // After the results are shown again: same scroll position, focus on the opener.
    // Narrow layouts only: the results were hidden, so put the scroll back.
    requestAnimationFrame(() => {
      if (o && !wide) window.scrollTo(0, o.scrollY)
      const back =
        (o && document.querySelector<HTMLElement>(`[data-opener="${CSS.escape(o.key)}"]`)) ||
        document.querySelector<HTMLElement>('.sources-search input[type="search"]')
      back?.focus({ preventScroll: !!o && !wide })
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

  const seriesShown = !!open && (view.status !== 'idle' || view.busyOther || wasBusy || cleared || !!series.startError)
  const openTracked = open
    ? tracked.find((t) => t.source === open.source && t.series_id === open.series_id) ?? null
    : null
  const summary = sources ? pageSummary(sources) : null
  const searchBlocked = remote && !SEARCH_REMOTE_ALLOWED

  const canLink = !(remote && !IMPORT_REMOTE_ALLOWED)
  const canSearch = !searchBlocked
  const mode: FindMode = !canSearch ? 'link' : !canLink ? 'search' : storedMode === 'link' ? 'link' : 'search'
  const useWebLink = canLink
    ? (url: string) => {
        setLinkHandoff((cur) => ({ url, n: (cur?.n ?? 0) + 1 }))
        setStoredMode('link')
      }
    : null

  return (
    <div className={`sources-page${wide ? ' wide' : ''}${seriesShown ? ' has-series' : ''}`}>
      <header className="page-head sources-head">
        <h2>Sources</h2>
        <p className="page-meta" data-testid="sources-summary">
          {summary ? (
            <>
              {summary.on} ·{' '}
              {summary.paused ? <Badge tone="warn">{summary.paused} paused</Badge> : '0 paused'}
            </>
          ) : loadError ? null : (
            'Loading…'
          )}
        </p>
      </header>
      <ErrorBanner error={loadError} />

      <Card className="sources-main" aria-label="Search or paste a link">
        {canLink && canSearch && (
          <FindModeSwitch value={mode} onChange={setStoredMode} />
        )}
        {canLink && (
          <div className="sources-find" hidden={mode !== 'link'}>
            <UrlBox display={display} onOpenSeries={openFromUrl} handoff={linkHandoff} />
          </div>
        )}
        {canSearch && sources && (
          <div className="sources-find" hidden={mode !== 'search'}>
            <SearchPanel
              sources={sources}
              remote={remote}
              job={search}
              resultsHidden={seriesShown && !wide}
              onOpen={openSeries}
              onUseLink={useWebLink}
            />
          </div>
        )}
        {!canSearch && <p className="muted">Searching sources is PC only for now.</p>}
        {!canLink && <p className="muted">Importing from a link is PC only for now.</p>}
      </Card>

      {seriesShown && open && (
        <SeriesPanel
          key={`${open.source}:${open.series_id}`}
          open={open}
          display={display(open.source)}
          job={series}
          view={view}
          tracked={openTracked}
          remote={remote}
          cleared={cleared}
          busyEnded={wasBusy && !view.busyOther && view.status === 'idle'}
          focusKey={focusKey}
          showBack={!wide && search.status === 'done' && !!search.result}
          onReload={reloadSeries}
          onClose={closeSeries}
          onUntrack={untrack}
          untrackBusy={!!openTracked && untrackBusy === `${openTracked.source}:${openTracked.series_id}`}
          sourceInfo={sources?.find((x) => x.name === open.source) ?? null}
          preselect={
            preselect && preselect.source === open.source && preselect.series_id === open.series_id
              ? preselect.chapter_id
              : null
          }
          phone={phone}
          onTracked={setTracked}
        />
      )}

      {(notifications.length > 0 || tracked.length > 0) && (
        <NewChapters
          notifications={notifications}
          tracked={tracked}
          sources={sources}
          display={display}
          canAct={!remote || IMPORT_REMOTE_ALLOWED}
          onOpen={openSeries}
          onDismissed={(id) => setNotifications((ns) => ns.filter((n) => n.id !== id))}
          onUntrack={untrack}
          onTracked={setTracked}
          onChecked={loadTracking}
          untrackBusy={untrackBusy}
          untrackError={untrackError}
          clearUntrackError={() => setUntrackError(null)}
        />
      )}

      {sources && (
        <section aria-label="Source settings">
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
