import { useCallback, useEffect, useState } from 'react'

import { ApiError } from '../../../api/client'
import { isActiveStatus } from './autotuneGlossary'

// Polls a per-drama "run status" route (auto-tune, glossary-from-novel)
// while its run is queued or running. Status "idle" means "no run held in
// this app session" and reads as status null. refresh() re-reads at once (after a
// start or cancel) and restarts polling.
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

  useEffect(() => {
    let stopped = false
    let timer: ReturnType<typeof setTimeout> | undefined
    let failures = 0
    const run = () => {
      load(dramaId).then(
        (s) => {
          if (stopped) return
          failures = 0
          setState({ id: dramaId, status: s.status === 'idle' ? null : s, error: null, loaded: true })
          if (isActiveStatus(s.status)) timer = setTimeout(run, intervalMs)
        },
        (e: unknown) => {
          if (stopped) return
          // Network or 5xx: retry a few times before showing the error.
          const transient = !(e instanceof ApiError) || e.status === 0 || e.status >= 500
          failures += 1
          if (transient && failures < 5) {
            timer = setTimeout(run, intervalMs * 2 ** failures)
            return
          }
          setState((s) => ({ ...s, id: dramaId, error: e, loaded: true }))
        },
      )
    }
    run()
    return () => {
      stopped = true
      if (timer) clearTimeout(timer)
    }
  }, [dramaId, load, intervalMs, tick])

  const refresh = useCallback(() => setTick((n) => n + 1), [])
  const clearError = useCallback(() => setState((s) => ({ ...s, error: null })), [])
  const cur = state.id === dramaId ? state : { status: null, error: null, loaded: false }
  return { status: cur.status, error: cur.error, loaded: cur.loaded, refresh, clearError }
}
