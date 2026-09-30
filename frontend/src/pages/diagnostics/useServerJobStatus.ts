import { useCallback, useEffect, useRef, useState } from 'react'

import type { DiagnosticsJobState } from '../../types/diagnosticsInstalls'
import { JOB_POLL_MS, jobRunning } from './jobPoll'

/**
 * Loads a status that carries a server job (`{ job }`) and polls it every
 * JOB_POLL_MS while that job runs. `onFinished` fires once when a job this
 * page saw running stops (so the caller can refresh what it changed).
 */
export function useServerJobStatus<T extends { job: DiagnosticsJobState | null }>(
  load: () => Promise<T>, onFinished?: (s: T) => void,
) {
  const [status, setStatus] = useState<T | null>(null)
  const [error, setError] = useState<unknown>(null)
  const sawRunning = useRef(false)
  const finished = useRef(onFinished)
  useEffect(() => {
    finished.current = onFinished
  }, [onFinished])

  const refresh = useCallback(() =>
    load().then((s) => {
      setStatus(s)
      setError(null)
      if (jobRunning(s.job)) sawRunning.current = true
      else if (sawRunning.current) {
        sawRunning.current = false
        finished.current?.(s)
      }
    }, (e: unknown) => setError(e)), [load])

  useEffect(() => {
    void refresh()
  }, [refresh])
  const running = jobRunning(status?.job)
  useEffect(() => {
    if (!running) return
    const t = setInterval(() => void refresh(), JOB_POLL_MS)
    return () => clearInterval(t)
  }, [running, refresh])

  return { status, error, running, refresh }
}
