import { useEffect, useState } from 'react'

import { cancelJob } from '../../../../api/jobs'
import { getTranscribeConfig, startRetranscribeLine } from '../../../../api/workspace'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { useJob, useJobRun } from '../../../../hooks/useJob'
import { TERMINAL_STATUSES } from '../../../../types/jobs'
import { canRetranscribe, retranscribeDoneText } from './retranscribeLogic'

// Parity audit B1 (R23): re-run Whisper on just this line's audio window and
// replace its source text, for one misheard line without redoing the file.
// A GPU-queued job; shown only when the drama has audio. When it replaced
// the text, onChanged runs; reloadsEditor says whether that reloads the
// line editor (otherwise the open draft is stale and the user is told to
// reopen the line; saving a source-text edit from the stale draft is refused
// as a conflict, since the save sends the old text as its expected value).
export function RetranscribeLine({
  dramaId,
  lineId,
  onChanged,
  reloadsEditor = false,
}: {
  dramaId: number
  lineId: number
  onChanged?: () => void
  reloadsEditor?: boolean
}) {
  const [available, setAvailable] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [starting, setStarting] = useState(false)
  const [jobId, setJobId, runKey] = useJobRun()
  const [finished, setFinished] = useState<ReturnType<typeof retranscribeDoneText> | null>(null)

  useEffect(() => {
    let cancelled = false
    getTranscribeConfig(dramaId).then(
      (cfg) => !cancelled && setAvailable(canRetranscribe(cfg)),
      () => !cancelled && setAvailable(false),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId])

  const { job, error: pollError } = useJob(jobId, {
    runKey,
    onDone: (j) => {
      const r = retranscribeDoneText(j)
      setFinished(r)
      if (r.changed) onChanged?.()
    },
  })

  if (!available) return null
  const active = !!job && !TERMINAL_STATUSES.includes(job.status)
  const busy = starting || active || (!!jobId && !job && !pollError)

  const start = () => {
    setError(null)
    setFinished(null)
    setStarting(true)
    startRetranscribeLine(dramaId, lineId)
      .then((r) => setJobId(r.job_id), setError)
      .finally(() => setStarting(false))
  }

  return (
    <div className="review-origin" data-testid="retranscribe-line">
      <div className="review-actions">
        <button type="button" onClick={start} disabled={busy}>
          {busy ? 'Re-transcribing…' : 'Re-transcribe this line'}
        </button>
        {active && job && (
          <button type="button" onClick={() => cancelJob(job.job_id).catch(setError)}>
            Cancel
          </button>
        )}
      </div>
      <ErrorBanner error={error ?? pollError} onDismiss={() => setError(null)} />
      {active && job && (
        <p className="muted" data-testid="retranscribe-progress">
          {job.progress !== null && (
            <progress value={job.progress} max={1} aria-label="Re-transcribe progress" />
          )}{' '}
          {job.status === 'queued' ? 'Waiting for the GPU…' : job.message || 'Working…'}
        </p>
      )}
      {finished && (
        <p
          data-testid="retranscribe-result"
          className={finished.ok ? 'muted' : 'error'}
          role={finished.ok ? 'status' : 'alert'}
        >
          {finished.text}
          {finished.changed && !reloadsEditor && ' Close this editor and reopen the line to see it.'}
        </p>
      )}
      {!active && !finished && (
        <p className="muted">
          Runs speech recognition again on just this line’s audio and replaces its source text.
        </p>
      )}
    </div>
  )
}
