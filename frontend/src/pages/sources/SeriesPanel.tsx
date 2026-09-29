import { useEffect, useRef, useState } from 'react'

import { ErrorBanner } from '../../components/ErrorBanner'
import { ConfirmButton } from '../../components/ConfirmButton'
import { Section } from '../../components/Section'
import type { OpenSeries, SeriesResult, TrackedSeries } from '../../types/sources'
import { SourceErrorLine } from './SearchPanel'
import {
  CHAPTERS_PAGE, describeSourceError, groupChapters, limitGroups, percent, safeHref, seriesExtra, seriesMeta,
  type SeriesView,
} from './sourcesFormat'
import type { SourcesJob } from './useSourcesJob'

type Props = {
  open: OpenSeries
  display: string
  job: SourcesJob<SeriesResult>
  // What may be shown for `open` (the job can hold another series).
  view: SeriesView
  tracked: TrackedSeries | null
  remote: boolean
  // Chapter list dropped after the source's Adult works switch changed.
  cleared: boolean
  // Another series was loading and has now stopped: offer Try again.
  busyEnded: boolean
  // Changes each time a series is opened: focus moves to the heading.
  focusKey: number
  // Phone/tablet: the panel replaces the results and starts with "‹ Results".
  showBack: boolean
  onReload: () => void
  onClose: () => void
  onUntrack: (t: TrackedSeries) => void
  untrackBusy: boolean
}

// A long description is clamped to 3 lines with More/Less.
const LONG_DESCRIPTION = 180

export function SeriesPanel({
  open, display, job, view, tracked, remote, cleared, busyEnded, focusKey, showBack, onReload, onClose, onUntrack, untrackBusy,
}: Props) {
  const headRef = useRef<HTMLHeadingElement>(null)
  const [more, setMore] = useState(false)
  const [shown, setShown] = useState(CHAPTERS_PAGE)
  // "Cancel it" was accepted for the other series; reset whenever busyOther changes.
  const [stopAsked, setStopAsked] = useState(false)
  const [busySeen, setBusySeen] = useState(view.busyOther)
  if (busySeen !== view.busyOther) {
    setBusySeen(view.busyOther)
    setStopAsked(false)
  }
  const askStop = () => {
    void job.cancel().then((ok) => ok && setStopAsked(true))
  }

  useEffect(() => {
    if (focusKey) headRef.current?.focus({ preventScroll: false })
  }, [focusKey])

  const result = !cleared && view.status === 'done' ? view.result : null
  const info = result?.info ?? null
  const chapters = result?.chapters ?? []
  const groups = groupChapters(chapters)
  const title = info?.title || open.title || open.series_id
  const running = view.status === 'running'
  const siteUrl = safeHref(info?.url)
  const description = info?.description?.trim() ?? ''
  const extra = seriesExtra(info)

  return (
    <section className="panel sources-series" aria-label="Series">
      {showBack && (
        <button type="button" className="link sources-back" onClick={onClose}>
          ‹ Results
        </button>
      )}
      <div className="sources-series-head">
        <h3 ref={headRef} tabIndex={-1}>
          {title}
        </h3>
        {tracked && <span className="badge">Tracked</span>}
      </div>

      {view.busyOther ? (
        <div className="sources-busy">
          <p className="warn" role="alert">
            Another series from {display} is still loading.
          </p>
          <p aria-live="polite" className="muted">
            {stopAsked ? 'Asked the other series to stop. Try again in a moment.' : ''}
          </p>
          <div className="actions">
            {!stopAsked && (
              <button type="button" onClick={askStop}>
                Cancel it
              </button>
            )}
            <button type="button" onClick={onReload}>
              Try again
            </button>
          </div>
        </div>
      ) : (
        <ErrorBanner error={job.startError} onDismiss={job.clearStartError} describe={{ serverText: true }} />
      )}

      <div aria-live="polite">
        {running && (
          <p>
            {job.message || 'Loading the series…'}
            {percent(job.progress)} ·{' '}
            <button type="button" className="link" onClick={job.cancel}>
              Cancel
            </button>
          </p>
        )}
      </div>

      {busyEnded && (
        <p className="muted">
          The other series stopped loading.{' '}
          <button type="button" className="link" onClick={onReload}>
            Try again
          </button>
        </p>
      )}

      {cleared && <p className="muted">Chapter list cleared; reload it to use the new setting.</p>}

      {!cleared && view.status === 'error' && !!view.error && (
        <SourceErrorLine copy={{ ...describeSourceError(view.error, display, remote), retry: true }} onRetry={onReload} />
      )}

      {result && (
        <>
          <p className="sources-meta">{seriesMeta(display, info, chapters.length)}</p>
          {extra && <p className="muted">{extra}</p>}
          {description && (
            <div className="sources-description">
              <p className={more ? '' : 'clamp'}>{description}</p>
              {description.length > LONG_DESCRIPTION && (
                <button type="button" className="sources-more" aria-expanded={more} onClick={() => setMore((m) => !m)}>
                  {more ? 'Less' : 'More'}
                </button>
              )}
            </div>
          )}
        </>
      )}

      <div className="actions">
        {siteUrl && (
          <a href={siteUrl} target="_blank" rel="noopener noreferrer">
            Open on site ↗
          </a>
        )}
        {!running && (result || cleared) && (
          <button type="button" onClick={onReload}>
            Reload
          </button>
        )}
        {tracked && (
          <ConfirmButton
            label="Stop tracking…"
            verb="stop tracking"
            name={tracked.title || title}
            busy={untrackBusy}
            onConfirm={() => onUntrack(tracked)}
          />
        )}
        <button type="button" className="link" onClick={onClose}>
          Close
        </button>
      </div>

      {result && chapters.length > 0 && (
        <Section title="Chapters" count={chapters.length} defaultOpen storageKey="sources.chapters">
          <ChapterList groups={groups} shown={shown} />
          {chapters.length > shown && (
            <button type="button" onClick={() => setShown(chapters.length)}>
              Show all {chapters.length}
            </button>
          )}
        </Section>
      )}
      {showBack && result && (
        <button type="button" className="link sources-back" onClick={onClose}>
          ‹ Results
        </button>
      )}
    </section>
  )
}

function ChapterList({ groups, shown }: { groups: ReturnType<typeof groupChapters>; shown: number }) {
  const many = groups.length > 1
  return (
    <div className="sources-chapters">
      {limitGroups(groups, shown).map((g) => (
        <div key={g.group || '-'}>
          {many && <h4>{g.group || 'Other'}</h4>}
          <ul>
            {g.chapters.map((c) => (
              <li key={c.chapter_id}>{c.title || c.chapter_id}</li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  )
}
