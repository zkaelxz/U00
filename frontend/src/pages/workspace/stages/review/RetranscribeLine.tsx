import { useEffect, useState } from 'react'

import { cancelJob } from '../../../../api/jobs'
import { applyRetranscribeLine, getTranscribeConfig, startRetranscribeLine } from '../../../../api/workspace'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { useJob, useJobRun } from '../../../../hooks/useJob'
import { TERMINAL_STATUSES } from '../../../../types/jobs'
import { canRetranscribe, retranscribeOutcome, type RetranscribeOutcome } from './retranscribeLogic'

// Parity audit B1 (R23): re-run Whisper on just this line's audio window, for
// one misheard line without redoing the file. A GPU-queued job that writes
// nothing: when it finishes, "Heard" is shown next to the current text, and
// only "Use this" writes it (the server refuses with a 409 if the line was
// edited since the job started). Shown only when the drama has audio.
// onChanged runs after "Use this"; reloadsEditor says whether that reloads
// the line editor (otherwise the user is told to reopen the line).
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
  const [applying, setApplying] = useState(false)
  const [applied, setApplied] = useState(false)
  const [jobId, setJobId, runKey] = useJobRun()
  const [outcome, setOutcome] = useState<RetranscribeOutcome | null>(null)

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
    onDone: (j) => setOutcome(retranscribeOutcome(j)),
  })

  if (!available) return null
  const active = !!job && !TERMINAL_STATUSES.includes(job.status)
  const busy = starting || active || (!!jobId && !job && !pollError)

  const start = () => {
    setError(null)
    setOutcome(null)
    setApplied(false)
    setStarting(true)
    startRetranscribeLine(dramaId, lineId)
      .then((r) => setJobId(r.job_id), setError)
      .finally(() => setStarting(false))
  }

  const useThis = () => {
    if (!jobId || outcome?.kind !== 'proposal') return
    setError(null)
    setApplying(true)
    applyRetranscribeLine(dramaId, lineId, { job_id: jobId, expected_zh: outcome.base })
      .then(() => {
        setOutcome(null)
        setApplied(true)
        onChanged?.()
      }, setError)
      .finally(() => setApplying(false))
  }

  return (
    <div className="review-origin" data-testid="retranscribe-line">
      <div className="review-actions">
        <button type="button" onClick={start} disabled={busy || applying}>
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
      {outcome?.kind === 'proposal' && (
        <div data-testid="retranscribe-proposal">
          <dl className="review-origin-list">
            <div>
              <dt>Now</dt>
              <dd lang="zh">{outcome.base || <span className="muted">(empty)</span>}</dd>
            </div>
            <div>
              <dt>Heard</dt>
              <dd lang="zh" data-testid="retranscribe-heard">{outcome.proposed}</dd>
            </div>
          </dl>
          {outcome.same ? (
            <p className="muted">Whisper heard the same text.</p>
          ) : (
            <div className="review-actions">
              <button type="button" onClick={useThis} disabled={applying}>
                Use this
              </button>
              <button type="button" onClick={() => setOutcome(null)} disabled={applying}>
                Discard
              </button>
            </div>
          )}
        </div>
      )}
      {outcome?.kind === 'none' && (
        <p className="error" role="alert" data-testid="retranscribe-result">
          {outcome.text}
        </p>
      )}
      {applied && (
        <p className="muted" role="status" data-testid="retranscribe-result">
          Replaced this line’s source text.
          {!reloadsEditor && ' Close this editor and reopen the line to see it.'}
        </p>
      )}
      {!active && !outcome && !applied && (
        <p className="muted">
          Runs speech recognition again on just this line’s audio and shows what it heard before
          anything changes.
        </p>
      )}
    </div>
  )
}
