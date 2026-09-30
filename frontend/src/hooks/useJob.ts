import { useCallback, useEffect, useRef, useState } from 'react'

import { ApiError } from '../api/client'
import { getJob } from '../api/jobs'
import { TERMINAL_STATUSES, type JobRecord } from '../types/jobs'
import { useEventStream } from './useEventStream'

export interface PollOptions {
  intervalMs?: number
  fetchJob?: (id: string) => Promise<JobRecord>
  onUpdate: (job: JobRecord) => void
  onError: (err: ApiError) => void
  onDone?: (job: JobRecord) => void
  // Consecutive transient failures tolerated before onError (default 5).
  maxFailures?: number
  // Cap for the retry backoff (default 15000).
  maxBackoffMs?: number
  // One successful read, then stop (the push stream carries the rest).
  once?: boolean
}

// Network errors (status 0) and 5xx are worth retrying; 4xx (e.g. 404, the
// job is gone) will not fix themselves.
function isTransient(e: unknown): boolean {
  return !(e instanceof ApiError) || e.status === 0 || e.status >= 500
}

// Framework-free polling loop (testable with fake timers). Polls once
// immediately, then every intervalMs, until a terminal status or an
// error. Transient failures (network, 5xx) are retried with capped
// exponential backoff and only surfaced after maxFailures in a row; other
// errors (4xx) stop at once. Returns a stop function.
export function startJobPolling(id: string, opts: PollOptions): () => void {
  const { intervalMs = 1500, fetchJob = getJob, maxFailures = 5, maxBackoffMs = 15000 } = opts
  let failures = 0
  let stopped = false
  let timer: ReturnType<typeof setTimeout> | undefined

  const tick = async () => {
    try {
      const job = await fetchJob(id)
      if (stopped) return
      failures = 0
      opts.onUpdate(job)
      if (TERMINAL_STATUSES.includes(job.status)) {
        opts.onDone?.(job)
        return
      }
      if (opts.once) return
    } catch (e) {
      if (stopped) return
      failures += 1
      if (!isTransient(e) || failures >= maxFailures) {
        opts.onError(
          e instanceof ApiError
            ? e
            : new ApiError(0, { code: 'network_error', message: 'Lost contact with the API.' }),
        )
        return
      }
      timer = setTimeout(tick, Math.min(intervalMs * 2 ** failures, maxBackoffMs))
      return
    }
    timer = setTimeout(tick, intervalMs)
  }
  void tick()

  return () => {
    stopped = true
    if (timer) clearTimeout(timer)
  }
}

// Tracks the job a stage started. Job ids are fixed per drama (e.g. transcribe_3),
// so a second run reuses the id; every set bumps runKey so useJob restarts
// polling and drops the previous run's state instead of showing its stale "done".
export function useJobRun(): [string | null, (id: string | null) => void, number] {
  const [run, setRun] = useState<{ id: string | null; key: number }>({ id: null, key: 0 })
  const set = useCallback((id: string | null) => setRun((r) => ({ id, key: r.key + 1 })), [])
  return [run.id, set, run.key]
}

// State belongs to one (job id, run) pair; anything else reads as "no state yet".
export function pickRunState<T extends { id: string; runKey?: number }>(
  state: T | null,
  id: string | null,
  runKey: number | undefined,
): T | null {
  return state && state.id === id && state.runKey === runKey ? state : null
}

export function useJob(
  jobId: string | null,
  opts: { intervalMs?: number; onDone?: (job: JobRecord) => void; runKey?: number } = {},
) {
  // State is tagged with the id it belongs to, so switching id never shows
  // the previous job's data without needing a synchronous reset in the effect.
  const [state, setState] = useState<{ id: string; runKey?: number; job?: JobRecord; error?: ApiError } | null>(null)
  const onDoneRef = useRef(opts.onDone)
  // The run (id + runKey) that already finished: its later events and
  // resyncs are ignored, the way polling stopped at a terminal status.
  const finished = useRef<string | null>(null)
  const { intervalMs, runKey } = opts

  useEffect(() => {
    onDoneRef.current = opts.onDone
  })

  // Pushed updates (GET /api/events). A job's record arrives as its GET
  // returns it; a job that disappears is a 404, as a poll would see.
  const stream = useEventStream((type, data) => {
    if (!jobId) return
    const tag = runTag(jobId, runKey)
    if (finished.current === tag) return
    const pushed = data as Partial<JobRecord> | null
    if (pushed?.job_id !== jobId) return
    if (type === 'job') {
      const job = pushed as JobRecord
      setState({ id: jobId, runKey, job })
      if (TERMINAL_STATUSES.includes(job.status)) {
        finished.current = tag
        onDoneRef.current?.(job)
      }
    } else if (type === 'job_gone') {
      finished.current = tag
      const error = new ApiError(404, { code: 'not_found', message: 'No such job.' })
      setState((s) => ({ ...(s?.id === jobId && s.runKey === runKey ? s : { id: jobId, runKey }), error }))
    }
  })
  const polling = stream.mode === 'poll'

  // A new id or run starts unfinished (declared before the effect below,
  // so it runs first).
  useEffect(() => {
    finished.current = null
  }, [jobId, runKey])

  // One GET at start and after every (re)connect; the old polling loop only
  // while the stream is down.
  useEffect(() => {
    if (!jobId) return
    const tag = runTag(jobId, runKey)
    if (finished.current === tag) return
    return startJobPolling(jobId, {
      intervalMs,
      once: !polling,
      onUpdate: (job) => setState({ id: jobId, runKey, job }),
      onError: (error) => setState((s) => ({ ...(s?.id === jobId && s.runKey === runKey ? s : { id: jobId, runKey }), error })),
      onDone: (j) => {
        if (finished.current === tag) return
        finished.current = tag
        onDoneRef.current?.(j)
      },
    })
  }, [jobId, intervalMs, runKey, polling, stream.syncs])

  const cur = pickRunState(state, jobId, runKey)
  const job = cur?.job ?? null
  const status = job?.status ?? null
  return {
    job,
    status,
    error: cur?.error ?? null,
    done: status !== null && TERMINAL_STATUSES.includes(status),
  }
}

const runTag = (id: string, runKey: number | undefined) => `${id}\u0000${runKey ?? ''}`
