import { useCallback, useEffect, useState } from 'react'

import { ApiError } from '../../../api/client'
import { isActiveStatus } from './autotuneGlossary'

// Longest wait between retries after failures, so a server that comes back is noticed within a minute.
export const MAX_BACKOFF_MS = 30_000
// Transient failures are retried this many times before the error is shown.
export const SHOWN_AFTER_FAILURES = 5

export interface VisibilitySource {
  readonly hidden: boolean
  addEventListener(type: 'visibilitychange', fn: () => void): void
  removeEventListener(type: 'visibilitychange', fn: () => void): void
}

// The polling loop, apart from React so it can be tested with fake timers. Failures back off
// to a cap instead of stopping, and a hidden tab pauses until it is visible again.
export function startRunPolling<T extends { status: string }>(opts: {
  load: () => Promise<T>
  intervalMs: number
  onStatus: (s: T) => void
  onError: (e: unknown) => void
  doc: VisibilitySource
}): () => void {
  const { load, intervalMs, onStatus, onError, doc } = opts
  let stopped = false
  let timer: ReturnType<typeof setTimeout> | undefined
  let paused = false
  let failures = 0
  const run = () => {
    if (doc.hidden) {
      paused = true
      return
    }
    load().then(
      (s) => {
        if (stopped) return
        failures = 0
        onStatus(s)
        if (isActiveStatus(s.status)) timer = setTimeout(run, intervalMs)
      },
      (e: unknown) => {
        if (stopped) return
        failures += 1
        // Network or 5xx: retry a few times before showing the error.
        const transient = !(e instanceof ApiError) || e.status === 0 || e.status >= 500
        if (!transient || failures >= SHOWN_AFTER_FAILURES) onError(e)
        timer = setTimeout(run, Math.min(intervalMs * 2 ** failures, MAX_BACKOFF_MS))
      },
    )
  }
  const onVisible = () => {
    if (doc.hidden || !paused) return
    paused = false
    run()
  }
  doc.addEventListener('visibilitychange', onVisible)
  run()
  return () => {
    stopped = true
    if (timer) clearTimeout(timer)
    doc.removeEventListener('visibilitychange', onVisible)
  }
}

// Polls a per-drama "run status" route (auto-tune, glossary-from-novel)
// while its run is queued or running. Status "idle" means "no run held in
// this app session" and reads as status null. refresh() re-reads at once (after a
// start or cancel) and restarts polling; clearError() does the same so a dismissed
// error does not leave polling dead.
export function useRunStatus<T extends { status: string }>(
  dramaId: number,
  load: (dramaId: number) => Promise<T>,
  intervalMs = 1500,
) {
  const [state, setState] = useState<{ id: number; status: T | null; error: unknown; loaded: boolean }>({
    id: dramaId,
    status: null,
    error: null,
    loaded: false,
  })
  const [tick, setTick] = useState(0)

  useEffect(
    () =>
      startRunPolling({
        load: () => load(dramaId),
        intervalMs,
        doc: document,
        onStatus: (s) => setState({ id: dramaId, status: s.status === 'idle' ? null : s, error: null, loaded: true }),
        // The last known status is stale once the error shows; keeping it would leave "Testing..." and Cancel up.
        onError: (e) => setState({ id: dramaId, status: null, error: e, loaded: true }),
      }),
    [dramaId, load, intervalMs, tick],
  )

  const refresh = useCallback(() => setTick((n) => n + 1), [])
  const clearError = useCallback(() => {
    setState((s) => ({ ...s, error: null }))
    setTick((n) => n + 1)
  }, [])
  const cur = state.id === dramaId ? state : { status: null, error: null, loaded: false }
  return { status: cur.status, error: cur.error, loaded: cur.loaded, refresh, clearError }
}
