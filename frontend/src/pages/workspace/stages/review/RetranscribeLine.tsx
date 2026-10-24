import { useEffect, useRef, useState } from 'react'

import { cancelJob, probeJob } from '../../../../api/jobs'
import { useCancelsAnyJob } from '../../../../hooks/useSession'
import {
  applyRetranscribeLine,
  getRetranscribeResult,
  getTranscribeConfig,
  startRetranscribeLine,
} from '../../../../api/workspace'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { useJob, useJobRun } from '../../../../hooks/useJob'
import { useStoredRun } from '../../../../hooks/useStoredRun'
import { isTerminalStatus, offersCancel } from '../../../../types/jobs'
import type { RetranscribeApplyResult, RetranscribeResult } from '../../../../types/workspace'
import { canRetranscribe, jobIsForLine, retranscribeJobId, retranscribeOutcome } from './retranscribeLogic'
import { buttonClass } from '../../../../components/uiClasses'
import { NeedsInstall } from '../../../readiness/NeedsInstall'
import { useAudioToolNeed } from '../../../readiness/useAudioToolNeed'
import { useCancellableEffect } from '../../../../hooks/useCancellableEffect'
import { useTitleTextLang } from '../../StageContext'

// Parity R23: re-run Whisper on just this line's audio window, for
// one misheard line without redoing the file. A GPU-queued job that writes
// nothing. The job record carries no line text: when it finishes, the
// proposal is read from GET .../retranscribe and shown raw ("Heard" next to
// "Now"), and only "Use this" writes it, sending back exactly the texts shown
// (the server refuses with a 409 if the line was edited since the job
// started, or the run isn't the one shown). Shown only when the drama has
// audio. onChanged gets the applied line text after "Use this"; reloadsEditor
// says whether that updates the line editor (otherwise the user is told to
// reopen the line).
export function RetranscribeLine({
  dramaId,
  lineId,
  onChanged,
  reloadsEditor = false,
  focusOnReady = false,
  onFocused,
}: {
  dramaId: number
  lineId: number
  onChanged?: (applied: RetranscribeApplyResult) => void
  reloadsEditor?: boolean
  // Set by the line menu's "Re-transcribe…": bring the button into view. It
  // only focuses; starting the GPU job still takes a button press.
  focusOnReady?: boolean
  onFocused?: () => void
}) {
  const textLang = useTitleTextLang()
  const [available, setAvailable] = useState(false)
  const need = useAudioToolNeed()
  const [error, setError] = useState<unknown>(null)
  const [starting, setStarting] = useState(false)
  const [applying, setApplying] = useState(false)
  const [applied, setApplied] = useState(false)
  const [jobId, setJobId, runKey, adoptJob] = useJobRun()
  const [proposal, setProposal] = useState<RetranscribeResult | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  // The proposal (or the run still going) for this line is still on the server after the page was left.
  useStoredRun({
    dramaId,
    kind: `retranscribe-line-${lineId}`,
    jobIds: [retranscribeJobId(dramaId)],
    // The id is shared with the many-line run; only a record that names this line is ours.
    adopt: (id) => void probeJob(id).then((j) => j?.result?.line_id === lineId && adoptJob(id), () => undefined),
    fetchResult: (id) => getRetranscribeResult(id, lineId),
    onResult: (r) => {
      if (r.line_id === lineId) setProposal((cur) => cur ?? r)
    },
    onError: setError,
    draftShape: {},
  })
  const startRef = useRef<HTMLButtonElement>(null)

  // The button only exists once the config fetch says audio is available.
  useEffect(() => {
    if (!focusOnReady || !available) return
    startRef.current?.scrollIntoView({ block: 'center' })
    startRef.current?.focus()
    onFocused?.()
  }, [focusOnReady, available, onFocused])

  useCancellableEffect((cancelled) => {
    getTranscribeConfig(dramaId).then(
      (cfg) => !cancelled() && setAvailable(canRetranscribe(cfg)),
      () => !cancelled() && setAvailable(false),
    )
  }, [dramaId])

  const anyJob = useCancelsAnyJob()
  const { job, error: pollError } = useJob(jobId, {
    runKey,
    onDone: (j) => {
      if (!jobIsForLine(j, lineId)) {
        setJobId(null) // another line's run: not ours to show
        return
      }
      const o = retranscribeOutcome(j)
      if (o.kind === 'none') {
        setFailure(o.text)
        return
      }
      getRetranscribeResult(dramaId, lineId).then((r) => {
        if (r.line_id === lineId) setProposal(r)
      }, setError)
    },
  })

  if (!available) return null
  const ours = jobIsForLine(job, lineId)
  const active = !!job && ours && !isTerminalStatus(job.status)
  const busy = starting || active || (!!jobId && !job && !pollError)

  const start = () => {
    setError(null)
    setProposal(null)
    setFailure(null)
    setApplied(false)
    setStarting(true)
    startRetranscribeLine(dramaId, lineId)
      .then((r) => setJobId(r.job_id), setError)
      .finally(() => setStarting(false))
  }

  const useThis = () => {
    if (!proposal) return
    setError(null)
    setApplying(true)
    applyRetranscribeLine(dramaId, lineId, {
      job_id: proposal.job_id,
      expected_zh: proposal.base_zh,
      expected_proposed: proposal.proposed_zh,
    })
      .then((r) => {
        setProposal(null)
        setApplied(true)
        onChanged?.(r)
      }, setError)
      .finally(() => setApplying(false))
  }

  const same = !!proposal && proposal.proposed_zh === proposal.base_zh

  return (
    <div className="review-origin" data-testid="retranscribe-line">
      <div className="review-actions">
        <button ref={startRef} type="button" onClick={start} disabled={busy || applying || !!need} title={need?.reason}>
          {busy ? 'Re-transcribing…' : 'Re-transcribe this line'}
        </button>
        {active && job && offersCancel(job, anyJob) && (
          <button type="button" className={buttonClass('secondary', 'sm')} onClick={() => cancelJob(job.job_id).catch(setError)}>
            Cancel
          </button>
        )}
      </div>
      {need && <NeedsInstall need={need} />}
      <ErrorBanner error={error ?? pollError} onDismiss={() => setError(null)} />
      {active && job && (
        <p className="muted" data-testid="retranscribe-progress">
          {job.progress !== null && (
            <progress value={job.progress} max={1} aria-label="Re-transcribe progress" />
          )}{' '}
          {job.status === 'queued' ? 'Waiting for the GPU…' : job.message || 'Working…'}
        </p>
      )}
      {proposal && (
        <div data-testid="retranscribe-proposal">
          <dl className="review-origin-list">
            <div>
              <dt>Now</dt>
              <dd lang={textLang}>{proposal.base_zh || <span className="muted">(empty)</span>}</dd>
            </div>
            <div>
              <dt>Heard</dt>
              <dd lang={textLang} data-testid="retranscribe-heard">{proposal.proposed_zh}</dd>
            </div>
          </dl>
          {same ? (
            <p className="muted">Whisper heard the same text.</p>
          ) : (
            <div className="review-actions">
              <button type="button" onClick={useThis} disabled={applying}>
                Use this
              </button>
              <button type="button" onClick={() => setProposal(null)} disabled={applying}>
                Discard
              </button>
            </div>
          )}
        </div>
      )}
      {failure && (
        <p className="error" role="alert" data-testid="retranscribe-result">
          {failure}
        </p>
      )}
      {applied && (
        <p className="muted" role="status" data-testid="retranscribe-result">
          Replaced this line’s source text.
          {!reloadsEditor && ' Close this editor and reopen the line to see it.'}
        </p>
      )}
      {!active && !proposal && !failure && !applied && (
        <p className="muted">
          Runs speech recognition again on just this line’s audio and shows what it heard before
          anything changes.
        </p>
      )}
    </div>
  )
}
