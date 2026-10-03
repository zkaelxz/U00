/*
 * One fixed-id Discover job (bulk extract or navigation help).
 *
 *   const job = useDiscoverJob(getBulkExtractResult)
 *   job.start(() => startBulkExtract(urls, label, engine))
 *
 * Uses the Sources polling loop (pollSourcesJob: 1.5 s, retries, an "idle"
 * answer on the first look means "never ran"). On mount it looks once, so a
 * result from earlier in this API process shows again. A start answered 429
 * means the same job is already running (the id is fixed), so it attaches to it.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { ApiError } from '../../api/client'
import type { DiscoverJobStarted } from '../../types/discover'
import type { SourcesJobResult } from '../../types/sources'
import { pollSourcesJob } from '../sources/useSourcesJob'

type DiscoverJobStatus = 'idle' | 'running' | 'done' | 'error'

interface State<R> {
  status: DiscoverJobStatus
  progress: number | null
  message: string | null
  result: R | null
  error: ApiError | null
}

const IDLE = { status: 'idle', progress: null, message: null, result: null, error: null } as const

export function useDiscoverJob<R>(jobId: string, fetchResult: () => Promise<SourcesJobResult<R>>) {
  const [state, setState] = useState<State<R>>(IDLE)
  const [startError, setStartError] = useState<unknown>(null)
  const [pollKey, setPollKey] = useState(0)
  const busy = useRef(false)
  // While a start is in flight, a look at the previous run must not show.
  const muted = useRef(false)
  const fetchRef = useRef(fetchResult)
  useEffect(() => {
    fetchRef.current = fetchResult
  })

  useEffect(
    () =>
      pollSourcesJob<R>(jobId, {
        fetchResult: () => fetchRef.current(),
        onUpdate: (r) =>
          !muted.current &&
          setState({
            status: r.status === 'done' ? 'done' : r.status === 'running' || r.status === 'queued' ? 'running' : 'error',
            progress: r.progress,
            message: r.message,
            result: r.status === 'done' ? r.result : null,
            error:
              r.status === 'done' || r.status === 'running' || r.status === 'queued'
                ? null
                : new ApiError(500, { code: 'application_error', message: r.message || 'The job failed.' }),
          }),
        onIdle: () => !muted.current && setState(IDLE),
        onError: (error) => !muted.current && setState((s) => ({ ...s, status: 'error', error })),
      }),
    [jobId, pollKey],
  )

  const start = useCallback((post: () => Promise<DiscoverJobStarted>) => {
    if (busy.current) return
    busy.current = true
    muted.current = true
    setStartError(null)
    setState({ ...IDLE, status: 'running' })
    const attach = () => {
      muted.current = false
      setPollKey((k) => k + 1)
    }
    post().then(
      () => {
        busy.current = false
        attach()
      },
      (e: unknown) => {
        busy.current = false
        if (e instanceof ApiError && e.status === 429) {
          attach()
          return
        }
        muted.current = false
        setState(IDLE)
        setStartError(e)
      },
    )
  }, [])

  return {
    ...state,
    startError,
    clearStartError: () => setStartError(null),
    start,
    reset: () => setState(IDLE),
  }
}
