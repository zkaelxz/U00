import { useEffect, useRef, useState } from 'react'

import { ApiError } from '../api/client'
import { getJob } from '../api/jobs'
import { TERMINAL_STATUSES, type JobRecord } from '../types/jobs'

export interface PollOptions {
  intervalMs?: number
  fetchJob?: (id: string) => Promise<JobRecord>
  onUpdate: (job: JobRecord) => void
  onError: (err: ApiError) => void
  onDone?: (job: JobRecord) => void
}

// Framework-free polling loop (testable with fake timers). Polls once
// immediately, then every intervalMs, until a terminal status or an
// error. Returns a stop function.
export function startJobPolling(id: string, opts: PollOptions): () => void {
  const { intervalMs = 1500, fetchJob = getJob } = opts
  let stopped = false
  let timer: ReturnType<typeof setTimeout> | undefined

  const tick = async () => {
    try {
      const job = await fetchJob(id)
      if (stopped) return
      opts.onUpdate(job)
      if (TERMINAL_STATUSES.includes(job.status)) {
        opts.onDone?.(job)
        return
      }
    } catch (e) {
      if (stopped) return
      opts.onError(
        e instanceof ApiError
          ? e
          : new ApiError(0, { code: 'network_error', message: 'Lost contact with the API.' }),
      )
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

export function useJob(
  jobId: string | null,
  opts: { intervalMs?: number; onDone?: (job: JobRecord) => void } = {},
) {
  // State is tagged with the id it belongs to, so switching id never shows
  // the previous job's data without needing a synchronous reset in the effect.
  const [state, setState] = useState<{ id: string; job?: JobRecord; error?: ApiError } | null>(null)
  const onDoneRef = useRef(opts.onDone)
  const { intervalMs } = opts

  useEffect(() => {
    onDoneRef.current = opts.onDone
  })

  useEffect(() => {
    if (!jobId) return
    return startJobPolling(jobId, {
      intervalMs,
      onUpdate: (job) => setState({ id: jobId, job }),
      onError: (error) => setState((s) => ({ ...(s?.id === jobId ? s : { id: jobId }), error })),
      onDone: (j) => onDoneRef.current?.(j),
    })
  }, [jobId, intervalMs])

  const cur = state && state.id === jobId ? state : null
  const job = cur?.job ?? null
  const status = job?.status ?? null
  return {
    job,
    status,
    error: cur?.error ?? null,
    done: status !== null && TERMINAL_STATUSES.includes(status),
  }
}
