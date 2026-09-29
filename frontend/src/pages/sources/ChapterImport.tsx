/*
 * Chapter import for the open series (S-4, R3) and "Track for new chapters"
 * (R4). SeriesPanel owns the ticked chapter ids; this file holds:
 *
 *   useChapterImport(source, seriesId)  (useChapterImport.ts) the remembered
 *                                       drama and the sourceimport_<drama> job
 *   <ImportSetup>  drama picker (+ New drama…), Track, progress, outcomes
 *   <ImportBar>    "Import N chapters" (sticky at the bottom of the list)
 *
 * The request carries ids only; the server re-reads the chapter list itself
 * and answers per chapter (imported / already there / failed / not found).
 * A finished result is shown only for a run started here: the job id is per
 * drama, so a result found on load may be another series' import.
 */
import { useEffect, useRef, useState } from 'react'

import { trackSeries } from '../../api/sourcesImport'
import { ErrorBanner } from '../../components/ErrorBanner'
import type { SeriesChapter, TrackedSeries } from '../../types/sources'
import { DramaPicker } from './DramaPicker'
import type { ChapterImportState } from './useChapterImport'
import { useDramaList } from './useDramaList'
import { describeSourceError, percent } from './sourcesFormat'
import {
  chapterImportDramas, comicNote, importIds, importLabel, importReason, outcomeSummary, outcomeText, outcomeTone,
} from './urlImportFormat'

type SetupProps = {
  imp: ChapterImportState
  source: string
  seriesId: string
  display: string
  comic: boolean
  title: string
  language: string | null
  tracked: TrackedSeries | null
  onTracked: (list: TrackedSeries[]) => void
}

export function ImportSetup({ imp, source, seriesId, display, comic, title, language, tracked, onTracked }: SetupProps) {
  const dramas = useDramaList()
  const [trackBusy, setTrackBusy] = useState(false)
  const [trackError, setTrackError] = useState<unknown>(null)
  const outcomesRef = useRef<HTMLDivElement>(null)
  const { job, running, shownResult } = imp

  // A run started here finished: bring its outcomes into view.
  useEffect(() => {
    if (shownResult) outcomesRef.current?.scrollIntoView({ block: 'nearest' })
  }, [shownResult])

  const track = () => {
    setTrackBusy(true)
    setTrackError(null)
    trackSeries(source, seriesId, imp.dramaId).then(
      (list) => {
        setTrackBusy(false)
        onTracked(list)
      },
      (e: unknown) => {
        setTrackBusy(false)
        setTrackError(e)
      },
    )
  }

  const failed = job.startedHere && job.status === 'error' ? job.error : null
  const note = shownResult ? comicNote(shownResult) : null
  return (
    <div className="sources-import" aria-label="Import" role="group">
      <DramaPicker
        dramas={dramas.items ? chapterImportDramas(dramas.items, comic) : null}
        value={imp.dramaId}
        onChange={imp.setDramaId}
        disabled={running}
        newDrama={{ title, language, comic }}
        onCreated={dramas.add}
        help={comic ? 'Comic pages go into a manhua, manga or manhwa drama.' : 'Chapter text is added to a novel drama’s text.'}
      />
      <ErrorBanner error={dramas.error} />
      {!tracked && (
        <div className="actions">
          <button type="button" disabled={trackBusy} onClick={track}>
            {trackBusy ? 'Tracking…' : 'Track for new chapters'}
          </button>
          <span className="muted">New chapters are listed under New chapters.</span>
        </div>
      )}
      <ErrorBanner error={trackError} onDismiss={() => setTrackError(null)} describe={{ serverText: true }} />
      <ErrorBanner error={job.startError} onDismiss={job.clearStartError} describe={{ serverText: true }} />

      <div aria-live="polite" ref={outcomesRef}>
        {running && (
          <p data-testid="import-progress">
            {job.message || 'Importing…'}
            {percent(job.progress)}
          </p>
        )}
        {failed && <p className="warn" role="alert">{describeSourceError(failed, display).text}</p>}
        {shownResult && (
          <div className="sources-outcomes" data-testid="import-outcomes">
            <p className={shownResult.failed_count ? 'warn' : undefined}>
              <strong>{outcomeSummary(shownResult)}</strong>
              {imp.dramaId && (
                <>
                  {' · '}
                  <a href={`#/drama/${imp.dramaId}/source`}>Open workspace</a>
                </>
              )}
            </p>
            {note && <p className="muted">{note}</p>}
            {!!shownResult.handoff && (
              <p className="warn">{display} showed a browser check, so the rest was not imported. Try again later.</p>
            )}
            <ul className="sources-outcome-list">
              {shownResult.chapters.map((c) => (
                <li key={c.chapter_id}>
                  <span>{c.title || c.chapter_id}</span>
                  <span className={outcomeTone(c.outcome)}>{outcomeText(c)}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  )
}

type BarProps = {
  imp: ChapterImportState
  chapters: SeriesChapter[]
  selected: string[]
  phone: boolean
}

export function ImportBar({ imp, chapters, selected, phone }: BarProps) {
  const count = importIds(selected, chapters).length
  const reason = importReason(count, imp.dramaId)
  return (
    <div className={`sources-import-bar${phone ? ' phone' : ''}`} data-testid="import-bar">
      <button
        type="button"
        className="primary"
        disabled={!!reason || imp.running}
        onClick={() => imp.start(chapters, selected)}
      >
        {imp.running ? 'Importing…' : importLabel(count)}
      </button>
      {imp.running ? (
        <button type="button" onClick={() => void imp.job.cancel()}>
          Cancel
        </button>
      ) : (
        reason && <span className="muted">{reason}</span>
      )}
    </div>
  )
}
