import { useEffect, useState } from 'react'

import { cancelJob } from '../../../api/jobs'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { safeDetail } from '../../../components/errorMessages'
import { capFirst } from '../../../labels'
import type { ApiError } from '../../../api/client'
import type { JobRecord } from '../../../types/jobs'
import { etaStage, formatLeft, isNoPercentStage, liveEtaSeconds, type EtaSample } from './transcribeEstimate'
import { formatElapsed } from './autotuneGlossary'
import { TERMINAL_STATUSES, jobFailed, jobOutcomeText } from '../../../types/jobs'

interface Props {
  job: JobRecord | null
  pollError: ApiError | null
  // Optional muted line under the status message.
  note?: string | null
  // Transcribe only: show elapsed time and, once the percent has moved for a
  // while, "about N min left".
  liveEta?: boolean
  // Transcribe only: the run's expected seconds from this PC's recorded speed,
  // shown as the ETA until the live readings settle.
  expectedSeconds?: number | null
}

// Elapsed time and the ETA for a running job; null until the job exists.
// Percent readings are kept per stage, so a new stage starts a fresh clock.
function useLiveProgress(job: JobRecord | null, enabled: boolean, expectedSeconds?: number | null) {
  const running = enabled && job !== null && !TERMINAL_STATUSES.includes(job.status)
  const [now, setNow] = useState(() => Date.now() / 1000)
  const [firstSeen, setFirstSeen] = useState<number | null>(null)
  const [samples, setSamples] = useState<{ stage: string; list: EtaSample[] }>({ stage: '0', list: [] })
  const progress = job?.progress ?? null
  const message = job?.message ?? ''
  useEffect(() => {
    if (!running) {
      setFirstSeen(null)
      setSamples({ stage: '0', list: [] })
      return
    }
    const t = Date.now() / 1000
    setNow(t)
    setFirstSeen((f) => f ?? t)
    const id = setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => clearInterval(id)
  }, [running])
  useEffect(() => {
    if (!running) return
    const t = Date.now() / 1000
    const stage = etaStage(message)
    setSamples((cur) => {
      const list = cur.stage === stage ? cur.list : []
      if (progress === null || progress <= 0 || isNoPercentStage(message)) return { stage, list }
      const last = list[list.length - 1]
      if (last && last.p === progress && t - last.t < 5) return { stage, list }
      return { stage, list: [...list, { t, p: progress }].slice(-60) }
    })
  }, [running, progress, message, now])
  if (!running || !job) return { elapsed: null, left: null }
  const started = job.started_at ?? firstSeen ?? now
  const left = isNoPercentStage(message) || samples.stage !== etaStage(message)
    ? null
    : liveEtaSeconds(samples.list, now, expectedSeconds)
  return { elapsed: Math.max(0, now - started), left }
}

export function JobPanel({ job, pollError, note, liveEta = false, expectedSeconds }: Props) {
  const { elapsed, left } = useLiveProgress(job, liveEta, expectedSeconds)
  const [cancelError, setCancelError] = useState<unknown>(null)
  const active = job !== null && !TERMINAL_STATUSES.includes(job.status)
  // Server text goes through safeDetail like job.error; if it is unsafe or
  // too long, the plain outcome label is still shown.
  const outcomeFull = job && job.status !== 'error' ? jobOutcomeText(job) : null
  const outcomeText = outcomeFull && job
    ? (safeDetail(outcomeFull) ?? jobOutcomeText({ outcome: job.outcome }))
    : null

  return (
    <section className="panel job-panel" aria-label="Job" data-testid="job-panel">
      <h3>Job</h3>
      <ErrorBanner error={pollError} />
      {job ? (
        <>
          <p data-testid="job-status">
            {capFirst(job.status)}
            {job.message ? ` · ${job.message}` : ''}
          </p>
          {elapsed !== null && !/\(elapsed /.test(job.message) && (
            <p className="muted" role="note" data-testid="job-elapsed">
              {formatElapsed(elapsed)} elapsed
              {left !== null && ` · ${formatLeft(left)}${/step 1 of 2/i.test(job.message) ? ' in this step' : ''}`}
            </p>
          )}
          {note && <p className="muted" data-testid="job-note">{note}</p>}
          {job.progress !== null && (
            <p>
              <progress value={job.progress} max={1} aria-label="Job progress" />{' '}
              <span data-testid="job-percent">{Math.round(Math.min(Math.max(job.progress, 0), 1) * 100)}%</span>
            </p>
          )}
          {job.status === 'error' && job.error && (
            <p className="error" role="alert">
              {safeDetail(job.error) ?? 'The job failed. Details are in the app log.'}
            </p>
          )}
          {outcomeText && (
            <p
              data-testid="job-outcome"
              className={jobFailed(job) ? 'error' : job.outcome === 'ok' ? 'muted' : undefined}
              style={!jobFailed(job) && job.outcome !== 'ok' ? { color: 'var(--warn)' } : undefined}
              role={jobFailed(job) || job.outcome !== 'ok' ? 'alert' : undefined}
            >
              {outcomeText}
            </p>
          )}
          {active && (
            <button
              type="button"
              onClick={() => cancelJob(job.job_id).then(() => setCancelError(null), setCancelError)}
            >
              Cancel job
            </button>
          )}
          <ErrorBanner error={cancelError} onDismiss={() => setCancelError(null)} />
        </>
      ) : (
        !pollError && <p className="muted">Starting…</p>
      )}
    </section>
  )
}
