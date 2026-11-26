/*
 * The Library's first screen: Ready (finished titles to watch or read), Needs you (what only a person can unblock,
 * stoppers first) and one Running line. A shelf with nothing in it is not drawn. A viewer who
 * can't control the PC queue (a household member) sees their own running and stopped titles under "Titles you
 * added" instead, with no buttons they could not use.
 */
import { useMemo } from 'react'

import { getHome } from '../api/library'
import { useHolds } from '../hooks/useHolds'
import { useJobs, useNow } from '../hooks/useJobs'
import { useLoad } from '../hooks/useLoad'
import { useMediaQuery } from '../hooks/useMediaQuery'
import { usePcOnly } from '../hooks/usePcOnly'
import { browserStorage, readPref } from '../hooks/usePersistedState'
import { freshServerPlace, newerPlace, positionKey, positionTimeKey, serverSeenKey } from '../pages/watch/watchLogic'
import { pluralLocale } from '../labels'
import { titleKind } from '../pages/workspace/titleKind'
import { routeHref } from '../router'
import type { LibraryHome } from '../types/library'
import { HelpTip } from './HelpTip'
import { activeCount } from './jobsMenuState'
import { parseTime } from './libraryView'
import { kindTag, needsRow, readyCard, readyOrder, runningLine, stepVerb, type NeedsViewer } from './homeShelfLogic'
import './homeShelves.css'

// The full list's own heading (LibraryList), so the jump needs no marker element of its own in the page's spaced stack.
const showAll = () => document.getElementById('library-heading')?.closest('section')?.scrollIntoView({ block: 'start' })

export function HomeShelves({ reloadKey, total }: { reloadKey: number; total: number | null }) {
  // Only a phone needs the jump: there the shelves fill the first screen and push the full list out of sight.
  const phone = useMediaQuery('(max-width: 640px)')
  const { jobs } = useJobs()
  const active = activeCount(jobs ?? [])
  // A step starting or ending moves titles between shelves; the jobs list already hears about it.
  const home = useLoad(getHome, reloadKey * 1000 + active)
  const pc = usePcOnly()
  const viewer: NeedsViewer = {
    canControlQueue: home.data?.can_control_queue ?? false,
    canOpenSettings: pc !== 'remote',
  }
  const now = useNow(active > 0)
  if (!home.data) return null
  return (
    <div className="home-shelves" data-testid="home-shelves">
      <ReadyShelf home={home.data} />
      {viewer.canControlQueue
        ? <>
            <NeedsShelf home={home.data} viewer={viewer} />
            <RunningRow home={home.data} jobs={jobs} now={now} />
          </>
        : <YoursShelf home={home.data} viewer={viewer} jobs={jobs} now={now} />}
      {phone && total !== null && total > 0 && (
        <button type="button" className="home-all" onClick={showAll}>
          <span>All titles</span><span className="muted">{total}</span>
        </button>
      )}
    </div>
  )
}

function seenOf(storage: ReturnType<typeof browserStorage>, id: number): number | null {
  const seen = readPref<number | null>(storage, serverSeenKey(id), null)
  return typeof seen === 'number' ? seen : null
}

function ReadyShelf({ home }: { home: LibraryHome }) {
  // The note points at the Watch page's check bar, which only someone who can edit lines gets, and only video and audio titles have.
  const canEditLines = useHolds('lines.edit')
  const storage = browserStorage()
  const cards = useMemo(() => readyOrder(home.ready.map((e) => ({
    e, ...readyCard(e, newerPlace(
      { seconds: Number(readPref(storage, positionKey(e.drama_id), 0)) || 0, at: Number(readPref(storage, positionTimeKey(e.drama_id), 0)) || null },
      freshServerPlace({ seconds: e.watched_seconds, at: parseTime(e.watched_at) || null }, seenOf(storage, e.drama_id)),
    )),
  }))), [home.ready, storage])
  if (cards.length === 0) return null
  return (
    <section className="home-shelf" aria-labelledby="home-ready">
      <header className="home-shelf-head">
        <h2 id="home-ready">Ready</h2>
        <span className="home-count ok">{home.ready_count}</span>
        <HelpTip label="Ready" id="home-ready-help">Finished titles you have not watched or read to the end. The ones you started come first.</HelpTip>
        {home.ready_count > cards.length && (
          <button type="button" className="home-more" onClick={showAll}>Show all {home.ready_count}</button>
        )}
      </header>
      <ul className="home-ready-grid">
        {cards.map(({ e, href, label, progress }) => (
          <li key={e.drama_id}>
            <a className="home-ready-card" href={href}>
              <span className="home-ready-art">
                <span className="home-tag">{kindTag(e)}</span>
                {progress === null
                  ? <span className="home-new">New</span>
                  : <span className="home-bar" aria-hidden="true"><span style={{ width: `${Math.round(progress * 100)}%` }} /></span>}
              </span>
              <span className="home-ready-title">{e.title}</span>
              {canEditLines && e.unsure_count > 0 && ['video', 'audio'].includes(titleKind(e.media_type, e.content_mode)) && <span className="home-ready-note">{pluralLocale(e.unsure_count, 'line')} to check</span>}
              <span className="home-ready-action">{label}</span>
            </a>
          </li>
        ))}
      </ul>
    </section>
  )
}

function NeedsShelf({ home, viewer }: { home: LibraryHome; viewer: NeedsViewer }) {
  const rows = home.needs_you.flatMap((e) => {
    const row = needsRow(e, viewer)
    return row ? [{ e, row }] : []
  })
  if (rows.length === 0) return null
  return (
    <section className="home-shelf" aria-labelledby="home-needs">
      <header className="home-shelf-head">
        <h2 id="home-needs">Needs you</h2>
        <span className="home-count warn">{rows.length}</span>
        <HelpTip label="Needs you" id="home-needs-help">Titles waiting for you: a step that stopped, or a series paused after its first episode.</HelpTip>
      </header>
      <ul className="home-list">
        {rows.map(({ e, row }) => (
          <li key={`${e.kind}-${e.drama_id ?? `s${e.series_id}`}`} className="home-list-row">
            <span className="home-list-text">
              <span className="home-list-title">{e.title}</span>
              <span className={e.kind === 'failed' ? 'home-list-why bad' : 'home-list-why'}>{row.text}</span>
            </span>
            {row.action && (
              <a className={e.kind === 'failed' ? 'home-act primary' : 'home-act'} href={row.action.href}>{row.action.label}</a>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}

function RunningRow({ home, jobs, now }: { home: LibraryHome; jobs: ReturnType<typeof useJobs>['jobs']; now: number }) {
  const line = runningLine(home.running, home.running_count, jobs, now)
  if (!line) return null
  return (
    <a className="home-running" href={routeHref({ name: 'jobs' })} data-testid="home-running">
      <span className="home-running-top">
        <span><strong>Running</strong> · {line.text}</span>
        {line.left && <span className="muted">{line.left}</span>}
      </span>
      {line.progress !== null && (
        <span className="home-bar wide" aria-hidden="true"><span style={{ width: `${Math.round(line.progress * 100)}%` }} /></span>
      )}
      <span className="home-running-foot">
        <span className="muted">{line.waiting ?? ''}</span>
        <span className="home-link">Open Jobs</span>
      </span>
    </a>
  )
}

function YoursShelf({ home, viewer, jobs, now }: { home: LibraryHome; viewer: NeedsViewer; jobs: ReturnType<typeof useJobs>['jobs']; now: number }) {
  const stopped = home.needs_you.filter((e) => e.kind === 'failed')
  if (home.running.length === 0 && stopped.length === 0) return null
  return (
    <section className="home-shelf" aria-labelledby="home-yours">
      <header className="home-shelf-head"><h2 id="home-yours">Titles you added</h2></header>
      <ul className="home-list">
        {home.running.map((e) => {
          const line = runningLine([e], 1, jobs, now)
          return (
            <li key={`r-${e.drama_id}-${e.step}`} className="home-list-row column">
              <span className="home-running-top">
                <span className="home-list-title">{e.title}</span>
                <span className="muted">{e.status === 'running' ? `${stepVerb(e.step)}${line?.left ? ` · ${line.left}` : ''}` : 'Waiting its turn'}</span>
              </span>
              {line?.progress != null && (
                <span className="home-bar wide" aria-hidden="true"><span style={{ width: `${Math.round(line.progress * 100)}%` }} /></span>
              )}
            </li>
          )
        })}
        {stopped.map((e) => (
          <li key={`f-${e.drama_id}`} className="home-list-row column">
            <span className="home-list-title">{e.title}</span>
            <span className="home-list-why bad">{needsRow(e, viewer)?.text}</span>
          </li>
        ))}
      </ul>
    </section>
  )
}
