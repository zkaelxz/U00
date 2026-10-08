/*
 * Chapter import for the open series (S-4, R3) and "Track for new chapters"
 * (R4). SeriesPanel owns the ticked chapter ids; this file holds:
 *
 *   useChapterImport(source, seriesId)  (useChapterImport.ts) the remembered
 *                                       drama and the sourceimport_<drama> job
 *   <ImportSetup>  drama picker (+ New drama…), Retry failed chapters (N),
 *                  Track, progress, outcomes
 *   <ImportBar>    "Import N chapters" (sticky at the bottom of the list)
 *   <TrackRow>     "Track for new chapters" (also for sources without import)
 *
 * The request carries ids only; the server re-reads the chapter list itself
 * and answers per chapter (imported / already there / failed / not found).
 * A finished result is shown only for a run started here: the job id is per
 * drama, so a result found on load may be another series' import.
 */
import { useEffect, useRef, useState } from 'react'

import { trackSeries } from '../../api/sourcesImport'
import { ButtonLink } from '../../components/Button'
import { ErrorBanner } from '../../components/ErrorBanner'
import { buttonClass } from '../../components/uiClasses'
import type { SeriesChapter, TrackedSeries } from '../../types/sources'
import { AiRecover } from './AiRecover'
import { DramaPicker } from './DramaPicker'
import { ExtractionReview } from './ExtractionReview'
import type { ChapterImportState } from './useChapterImport'
import { describeSourceError, percent } from './sourcesFormat'
import {
  comicNote, importIds, importLabel, importReason, outcomeSummary, outcomeText, needsAiRows, outcomeTone, retryLabel, retryNote,
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
  const { dramas } = imp
  const outcomesRef = useRef<HTMLDivElement>(null)
  const { job, running, shownResult } = imp
  const [reviewClosed, setReviewClosed] = useState(false)

  // A run started here finished: bring its outcomes into view.
  useEffect(() => {
    if (shownResult) outcomesRef.current?.scrollIntoView({ block: 'nearest' })
  }, [shownResult])

  const failed = job.startedHere && job.status === 'error' ? job.error : null
  const note = shownResult ? comicNote(shownResult) : null
  return (
    <div className="sources-import" aria-label="Import" role="group">
      <DramaPicker
        dramas={imp.choices}
        value={imp.dramaId}
        onChange={imp.setDramaId}
        disabled={running}
        newDrama={{ title, language, comic }}
        onCreated={dramas.add}
        hiddenCount={imp.hiddenCount}
        help={comic ? 'Comic pages go into a manhua, manga or manhwa drama.' : 'Chapter text is added to a novel drama’s text.'}
      />
      <ErrorBanner error={dramas.error} />
      <ErrorBanner error={imp.importState.error} describe={{ serverText: true }} />
      {imp.retry.length > 0 && (
        <div className="actions" data-testid="import-retry">
          <button type="button" className={buttonClass('secondary')} disabled={running} onClick={imp.startRetry}>
            {retryLabel(imp.retry.length)}
          </button>
          {retryNote(imp.retry.length) && <span className="muted">{retryNote(imp.retry.length)}</span>}
        </div>
      )}
      <AiRecover
        rows={needsAiRows(imp.importState.state)}
        disabled={running}
        onConfirm={(id, engine) => {
          setReviewClosed(false)
          imp.recover(id, engine)
        }}
      />
      {imp.recoveryReview && imp.dramaId && !reviewClosed && (
        <ExtractionReview dramaId={imp.dramaId} onClose={() => setReviewClosed(true)} />
      )}
      {!tracked && <TrackRow source={source} seriesId={seriesId} dramaId={imp.dramaId} onTracked={onTracked} />}
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
            <p className={shownResult.failed_count || shownResult.not_attempted_count ? 'warn' : undefined}>
              <strong>{outcomeSummary(shownResult)}</strong>
              {imp.dramaId && (
                <>
                  {' · '}
                  <ButtonLink href={`#/drama/${imp.dramaId}/source`} size="sm">
                    Open workspace
                  </ButtonLink>
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
        className={buttonClass('primary')}
        disabled={!!reason || imp.running}
        onClick={() => imp.start(chapters, selected)}
      >
        {imp.running ? 'Importing…' : importLabel(count)}
      </button>
      {imp.running ? (
        <button type="button" className={buttonClass('secondary')} onClick={() => void imp.job.cancel()}>
          Cancel
        </button>
      ) : (
        reason && <span className="muted">{reason}</span>
      )}
    </div>
  )
}

type TrackProps = {
  source: string
  seriesId: string
  // New chapters of a tracked series are imported into this drama (none: listed only).
  dramaId: number | null
  onTracked: (list: TrackedSeries[]) => void
}

export function TrackRow({ source, seriesId, dramaId, onTracked }: TrackProps) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const track = () => {
    setBusy(true)
    setError(null)
    trackSeries(source, seriesId, dramaId).then(
      (list) => {
        setBusy(false)
        onTracked(list)
      },
      (e: unknown) => {
        setBusy(false)
        setError(e)
      },
    )
  }
  return (
    <>
      <div className="actions">
        <button type="button" className={buttonClass('secondary')} disabled={busy} onClick={track}>
          {busy ? 'Tracking…' : 'Track for new chapters'}
        </button>
        <span className="muted">New chapters are listed under New chapters.</span>
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ serverText: true }} />
    </>
  )
}
