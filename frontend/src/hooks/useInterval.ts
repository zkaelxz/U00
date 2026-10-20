import { useEffect, useRef } from 'react'

/**
 * Calls `fn` every `ms` milliseconds, or never while `ms` is null. `fn` is read at each tick, so a new closure
 * does not restart the timer. With `pauseWhenHidden` a tick in a hidden tab is skipped; the timer keeps running
 * so polling resumes on its own when the tab is shown again.
 */
export function useInterval(fn: () => void, ms: number | null, opts: { pauseWhenHidden?: boolean } = {}) {
  const latest = useRef(fn)
  useEffect(() => {
    latest.current = fn
  })
  const { pauseWhenHidden = false } = opts
  useEffect(() => {
    if (ms === null) return
    const id = window.setInterval(() => {
      if (pauseWhenHidden && document.hidden) return
      latest.current()
    }, ms)
    return () => window.clearInterval(id)
  }, [ms, pauseWhenHidden, latest])
}
