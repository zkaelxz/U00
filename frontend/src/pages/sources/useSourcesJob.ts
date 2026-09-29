/*
 * One fixed-id Sources job (`sources_search`, or `sources_series_<name>`).
 *
 *   const job = useSourcesJob<SearchResult>('sources_search')
 *   job.start(() => startSearch(q))   // POST, then polls
 *   job.status  'idle' | 'running' | 'done' | 'error'
 *
 * Polls GET /api/sources/jobs/{id}/result every 1.5 s. On mount (and when
 * the id changes) it looks once: running -> keeps polling, done -> shows the
 * stored result, 404 -> idle. A failed job answers with its mapped error
 * (400/404/409/503), which ends polling. A network failure (status 0) or a
 * 500 is retried up to 3 times with backoff; after that a lost connection
 * reads "Lost contact with the API." and a 500 shows the server's error.
 * A start that gets 409 with details.job_id equal to this job reattaches
 * to the running one instead of showing an error.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { ApiError } from '../../api/client'
import { cancelJob } from '../../api/jobs'
import { getSourcesJobResult } from '../../api/sources'
import type { SourcesJobResult, SourcesJobStarted } from '../../types/sources'

export type SourcesJobStatus = 'idle' | 'running' | 'done' | 'error'

export const LOST_CONTACT = 'Lost contact with the API.'
export const POLL_MS = 1500
export const MAX_RETRIES = 3

export interface PollHandlers<R> {
  fetchResult?: (id: string) => Promise<SourcesJobResult<R>>
  intervalMs?: number
  onUpdate: (r: SourcesJobResult<R>) => void
  onIdle: () => void
  onError: (e: ApiError) => void
}

const isRetryable = (e: unknown) => !(e instanceof ApiError) || e.status === 0 || e.status === 500

/** Framework-free polling loop (fake-timer testable). Returns a stop function. */
export function pollSourcesJob<R>(id: string, h: PollHandlers<R>): () => void {
  const fetchResult = h.fetchResult ?? ((j: string) => getSourcesJobResult<R>(j))
  const interval = h.intervalMs ?? POLL_MS
  let failures = 0
  let seen = false
  let stopped = false
  let timer: ReturnType<typeof setTimeout> | undefined

  const tick = async () => {
    try {
      const r = await fetchResult(id)
      if (stopped) return
      failures = 0
      seen = true
      h.onUpdate(r)
      if (r.status !== 'running' && r.status !== 'queued') return
    } catch (e) {
      if (stopped) return
      if (e instanceof ApiError && e.status === 404 && !seen) {
        h.onIdle()
        return
      }
      if (isRetryable(e) && failures < MAX_RETRIES) {
        failures += 1
        timer = setTimeout(tick, interval * 2 ** failures)
        return
      }
      h.onError(
        e instanceof ApiError && e.status !== 0
          ? e
          : new ApiError(0, { code: 'network_error', message: LOST_CONTACT }),
      )
      return
    }
    timer = setTimeout(tick, interval)
  }
  void tick()
  return () => {
    stopped = true
    if (timer) clearTimeout(timer)
  }
}

/** True when a start's 409 is "this very job is already running". */
export function isSameJobConflict(e: unknown, jobId: string): boolean {
  if (!(e instanceof ApiError) || e.status !== 409) return false
  const d = e.details as { job_id?: unknown } | null | undefined
  return !!d && d.job_id === jobId
}

interface JobState<R> {
  id: string
  status: SourcesJobStatus
  progress: number | null
  message: string | null
  result: R | null
  jobStatus: string | null
  error: ApiError | null
}

const idle = <R>(id: string): JobState<R> => ({
  id, status: 'idle', progress: null, message: null, result: null, jobStatus: null, error: null,
})

export function useSourcesJob<R>(jobId: string | null) {
  const [state, setState] = useState<JobState<R> | null>(null)
  const [startError, setStartError] = useState<unknown>(null)
  const [starting, setStarting] = useState(false)
  // Bumped to (re)start polling for the current id: mount, a start, a reattach.
  const [pollKey, setPollKey] = useState(0)
  const idRef = useRef(jobId)
  // While a start is in flight, a look at the previous run must not show.
  const startingRef = useRef(false)
  useEffect(() => {
    idRef.current = jobId
  })

  useEffect(() => {
    if (!jobId) return
    return pollSourcesJob<R>(jobId, {
      onUpdate: (r) =>
        !startingRef.current &&
        setState({
          id: jobId,
          status: r.status === 'done' ? 'done' : r.status === 'running' || r.status === 'queued' ? 'running' : 'error',
          progress: r.progress,
          message: r.message,
          result: r.status === 'done' ? r.result : null,
          jobStatus: r.status,
          error:
            r.status === 'cancelled'
              ? new ApiError(409, { code: 'conflict', message: 'Cancelled.', details: { reason: 'CANCELLED' } })
              : null,
        }),
      onIdle: () => !startingRef.current && setState(idle(jobId)),
      onError: (error) => !startingRef.current && setState((s) => ({ ...(s && s.id === jobId ? s : idle<R>(jobId)), status: 'error', error })),
    })
  }, [jobId, pollKey])

  // `id` defaults to the hook's current id; pass it when the id changes in
  // the same event (opening a series on another source).
  const start = useCallback((post: () => Promise<SourcesJobStarted>, id = idRef.current) => {
    if (!id) return
    setStartError(null)
    setStarting(true)
    startingRef.current = true
    post().then(
      () => {
        startingRef.current = false
        setStarting(false)
        setState({ ...idle<R>(id), status: 'running' })
        setPollKey((k) => k + 1)
      },
      (e: unknown) => {
        startingRef.current = false
        setStarting(false)
        if (isSameJobConflict(e, id)) {
          setState({ ...idle<R>(id), status: 'running' })
          setPollKey((k) => k + 1)
        } else setStartError(e)
      },
    )
  }, [])

  const cancel = useCallback(() => {
    const id = idRef.current
    if (!id) return
    cancelJob(id).catch((e: unknown) => setStartError(e))
  }, [])

  /** Forget the shown result locally (the server keeps it). */
  const reset = useCallback(() => {
    const id = idRef.current
    if (id) setState(idle(id))
    setStartError(null)
  }, [])

  const cur = state && state.id === jobId ? state : null
  return {
    status: (starting ? 'running' : cur?.status ?? 'idle') as SourcesJobStatus,
    progress: cur?.progress ?? null,
    message: cur?.message ?? null,
    result: cur?.result ?? null,
    error: cur?.error ?? null,
    startError,
    clearStartError: () => setStartError(null),
    start,
    cancel,
    reset,
  }
}

export type SourcesJob<R> = ReturnType<typeof useSourcesJob<R>>
