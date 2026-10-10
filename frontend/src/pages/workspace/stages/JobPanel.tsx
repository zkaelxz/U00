import { useEffect, useRef, useState } from 'react'

import { cancelJob } from '../../../api/jobs'
import { Badge } from '../../../components/Badge'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { safeDetail } from '../../../components/errorMessages'
import { buttonClass } from '../../../components/uiClasses'
import { useJobs, useNow } from '../../../hooks/useJobs'
import { capFirst } from '../../../labels'
import type { ApiError } from '../../../api/client'
import type { JobRecord } from '../../../types/jobs'
import { relativeTime } from '../../jobs/jobsFilter'
import { etaStage, formatLeft, isNoPercentStage, liveEtaSeconds, type EtaSample } from './transcribeEstimate'
import { formatElapsed } from './autotuneGlossary'
import { stripStallNote, stuckText, watchStuck, type StuckInfo } from './jobStuck'
import { lastRunCounts, lastRunFor } from './lastRun'
import { TERMINAL_STATUSES, jobFailed, jobName, jobOutcomeText } from '../../../types/jobs'
import './jobPanel.css'

export interface LastRunProps {
  dramaId: number
  // The stage's own job ids (what useReattachJob gets): the card shows the
  // newest finished one of these.
  ids: readonly string[]
  // The stage's start function for a record it can run again with its current
  // form, or null when it can't (a job another panel starts); no Retry then.
  retryFor?: (job: JobRecord) => (() => void) | null
}

interface Props {
  job: JobRecord | null
  pollError: ApiError | null
  // The id the stage tracks, null when none. With `lastRun`, null shows the
  // stage's last finished run instead of "Starting…".
  jobId?: string | null
  lastRun?: LastRunProps
  // Optional muted line under the status message.
  note?: string | null
  // Transcribe only: show elapsed time and, once the percent has moved for a
  // while, "about N min left".
  liveEta?: boolean
  // Transcribe only: the run's expected seconds from this PC's recorded speed,
  // shown as the ETA until the live readings settle.
  expectedSeconds?: number | null
  // False hides Cancel (a remote admin may stop only their own jobs); default true.
  canCancel?: boolean
  // Runs after a Cancel request succeeds, so a caller can refresh its job list.
  onCancelled?: () => void
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

// Minutes the browser has seen no change in a running job (jobStuck.ts); null
// while nothing runs. One watch per job id, so a rerun starts a fresh count.
function useStuck(job: JobRecord | null): StuckInfo | null {
  const [info, setInfo] = useState<StuckInfo | null>(null)
  const latest = useRef(job)
  const watch = useRef<ReturnType<typeof watchStuck> | null>(null)
  const jobId = job?.job_id ?? null
  useEffect(() => {
    if (!jobId) {
      setInfo(null)
      return
    }
    watch.current = watchStuck(() => latest.current, setInfo)
    return () => {
      watch.current?.stop()
      watch.current = null
    }
  }, [jobId])
  useEffect(() => {
    latest.current = job
    watch.current?.update()
  }, [job])
  return info
}

export function JobPanel({ job, pollError, jobId, lastRun, note, liveEta = false, expectedSeconds, canCancel = true, onCancelled }: Props) {
  const { elapsed, left } = useLiveProgress(job, liveEta, expectedSeconds)
  const stuck = useStuck(job)
  const [cancelError, setCancelError] = useState<unknown>(null)
  const active = job !== null && !TERMINAL_STATUSES.includes(job.status)
  // Server text goes through safeDetail like job.error; if it is unsafe or
  // too long, the plain outcome label is still shown.
  const outcomeFull = job && job.status !== 'error' ? jobOutcomeText(job) : null
  const outcomeText = outcomeFull && job
    ? (safeDetail(outcomeFull) ?? jobOutcomeText({ outcome: job.outcome }))
    : null

  if (lastRun && jobId === null && !job && !pollError) return <LastRunCard {...lastRun} />

  const message = job ? stripStallNote(job.message) : ''
  return (
    <section className="panel job-panel" aria-label="Job" data-testid="job-panel">
      <h3>Job</h3>
      <ErrorBanner error={pollError} />
      {job ? (
        <>
          <p data-testid="job-status">
            {capFirst(job.status)}
            {message && job.status !== 'error' ? ` · ${message}` : ''}
          </p>
          {elapsed !== null && !/\(elapsed /.test(job.message) && (
            <p className="muted" role="note" data-testid="job-elapsed">
              {formatElapsed(elapsed)} elapsed
              {left !== null && ` · ${formatLeft(left)}${/step 1 of 2/i.test(job.message) ? ' in this step' : ''}`}
            </p>
          )}
          {note && <p className="muted" data-testid="job-note">{note}</p>}
          {job.progress !== null && job.status !== 'error' && (
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
              className={jobFailed(job) ? 'error' : job.outcome === 'ok' ? 'muted' : 'warn'}
              role={jobFailed(job) || job.outcome !== 'ok' ? 'alert' : undefined}
            >
              {outcomeText}
            </p>
          )}
          {active && (stuck?.stuck || canCancel) && (
            <div className="job-panel-actions">
              {stuck?.stuck && (
                <p className="warn job-stuck" role="alert" data-testid="job-stuck">
                  {stuckText(stuck, job.job_id)} <a href="#/jobs">All jobs</a>
                </p>
              )}
              {canCancel && (
                <button
                  type="button"
                  className={buttonClass('secondary', 'sm', 'job-panel-cancel')}
                  aria-label={`Cancel ${jobName(job)}`}
                  onClick={() => cancelJob(job.job_id).then(() => {
                    setCancelError(null)
                    onCancelled?.()
                  }, setCancelError)}
                >
                  Cancel
                </button>
              )}
            </div>
          )}
          <ErrorBanner error={cancelError} onDismiss={() => setCancelError(null)} />
        </>
      ) : (
        !pollError && <p className="muted">Starting…</p>
      )}
    </section>
  )
}

const STATUS_LABEL = { done: 'Done', failed: 'Failed', cancelled: 'Cancelled' } as const

function lastRunStatus(job: JobRecord): keyof typeof STATUS_LABEL {
  if (job.status === 'error' || job.outcome === 'failed') return 'failed'
  if (job.status === 'cancelled' || job.outcome === 'cancelled') return 'cancelled'
  return 'done'
}

// What the stage's last run did, from the app-wide jobs list, so leaving the
// stage doesn't lose the result. Nothing is rendered when no run has finished
// (rule 8: no empty panels).
function LastRunCard({ dramaId, ids, retryFor }: LastRunProps) {
  const { jobs } = useJobs()
  const now = useNow(true)
  const job = lastRunFor(jobs, dramaId, ids)
  if (!job) return null
  const status = lastRunStatus(job)
  const outcome = jobOutcomeText(job)
  // Server text goes through safeDetail, as the live panel does with job.error.
  const text = status === 'failed' && job.error
    ? (safeDetail(job.error) ?? 'The job failed. Details are in the app log.')
    : outcome
      ? (safeDetail(outcome) ?? jobOutcomeText({ outcome: job.outcome }))
      : stripStallNote(job.message)
  const counts = lastRunCounts(job)
  const retry = retryFor?.(job) ?? null
  const ended = job.finished_at ?? job.updated_at
  return (
    <section className="panel job-panel last-run" aria-label="Last run" data-testid="last-run">
      <h3>Last run</h3>
      <p className="last-run-head">
        <Badge tone={status === 'done' ? 'ok' : status === 'failed' ? 'bad' : 'warn'}>{STATUS_LABEL[status]}</Badge>
        {ended != null && (
          <time className="muted" dateTime={new Date(ended * 1000).toISOString()} data-testid="last-run-when">
            {relativeTime(ended, now)}
          </time>
        )}
      </p>
      {text && (
        <p className={status === 'failed' ? 'error' : undefined} data-testid="last-run-text">{text}</p>
      )}
      {counts && <p className="muted" data-testid="last-run-counts">{counts}</p>}
      <div className="job-panel-actions">
        {retry && (
          <button type="button" className={buttonClass('secondary')} onClick={retry} data-testid="last-run-retry">
            Retry
          </button>
        )}
        <a href="#/jobs">All jobs</a>
      </div>
    </section>
  )
}
