import { useEffect, useState } from 'react'

import { cancelJob } from '../../../../api/jobs'
import { getTimingCheck, snapToSpeech, startTimingCheck } from '../../../../api/timingCheck'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { buttonClass } from '../../../../components/uiClasses'
import { useJob, useJobRun } from '../../../../hooks/useJob'
import { TERMINAL_STATUSES } from '../../../../types/jobs'
import type { TimingCheckStatus } from '../../../../types/timingCheck'
import { checkSummary, snapSummary } from './timingCheckLogic'

// Compares the lines' times with the speech found in the audio and flags the
// ones that disagree. Writes only flags and flag notes; "Snap all flagged"
// changes start and end after a history snapshot, for lines unchanged since the check.
export function TimingCheck({ dramaId, onDone }: { dramaId: number; onDone: () => void }) {
  const [status, setStatus] = useState<TimingCheckStatus | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [starting, setStarting] = useState(false)
  const [snapping, setSnapping] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [jobId, setJobId, runKey, adoptJob] = useJobRun()

  const load = () => getTimingCheck(dramaId).then(setStatus, setError)
  useEffect(() => {
    let live = true
    // A check the app started after a transcription may already be running.
    getTimingCheck(dramaId).then(
      (s) => {
        if (!live) return
        setStatus(s)
        if (s.job_id && !TERMINAL_STATUSES.includes(s.status)) adoptJob(s.job_id)
      },
      (e: unknown) => live && setError(e),
    )
    return () => {
      live = false
    }
  }, [dramaId, adoptJob])

  const { job, error: pollError } = useJob(jobId, {
    runKey,
    onDone: (j) => {
      if (j.outcome === 'failed') setError(new Error(j.outcome_message ?? 'The timing check failed.'))
      load()
      onDone()
    },
  })
  const active = !!job && !TERMINAL_STATUSES.includes(job.status)
  const busy = starting || active || (!!jobId && !job && !pollError)
  const suggestionCount = status?.suggestions.length ?? 0

  const start = () => {
    setError(null)
    setNote(null)
    setStarting(true)
    startTimingCheck(dramaId)
      .then((r) => setJobId(r.job_id), setError)
      .finally(() => setStarting(false))
  }

  const snapAll = () => {
    setError(null)
    setSnapping(true)
    snapToSpeech(dramaId)
      .then((r) => {
        setNote(snapSummary(r))
        load()
        onDone()
      }, setError)
      .finally(() => setSnapping(false))
  }

  const summary = checkSummary(status ?? { last_check: null })
  return (
    <div className="review-flag-row" data-testid="timing-check">
      <button type="button" className={buttonClass('secondary')} disabled={busy || snapping} onClick={start}>
        {busy ? 'Checking timing…' : 'Check timing'}
      </button>
      {active && job && <button type="button" className={buttonClass('secondary')} onClick={() => cancelJob(job.job_id).catch(setError)}>Cancel</button>}
      <button type="button" className={buttonClass('secondary')} disabled={busy || snapping || suggestionCount === 0} onClick={snapAll}>
        Snap all flagged{suggestionCount ? ` (${suggestionCount})` : ''}
      </button>
      <span className="muted">
        Flags lines that start or end away from the speech in the audio (a quick speech-detection pass; no transcription, no GPU).
        Music can pass for speech, so check flagged lines by ear. Only flags and notes are written; “Snap” shortens a line to the speech after a history snapshot.
      </span>
      <ErrorBanner error={error ?? pollError} onDismiss={() => setError(null)} />
      {active && job && (
        <span role="status" data-testid="timing-progress">
          {job.progress !== null && <progress value={job.progress} max={1} aria-label="Timing check progress" />} {job.message || 'Working…'}
        </span>
      )}
      {!active && summary && <span role="status" data-testid="timing-summary">{summary}</span>}
      {note && <span role="status" data-testid="timing-snap-note">{note}</span>}
    </div>
  )
}
