/*
 * useAction: one Reader action (an AI tool, a lookup, a save) with its busy
 * state and error. The last call's arguments are remembered so "Try again"
 * (shown by ActionError after a 429) repeats it.
 */
import { useCallback, useRef, useState } from 'react'
import { useLatest } from '../../hooks/useLatest'

export interface Action<A extends unknown[]> {
  run: (...args: A) => Promise<void>
  retry: () => void
  busy: boolean
  error: unknown
  clearError: () => void
}

export function useAction<A extends unknown[]>(fn: (...args: A) => Promise<void>): Action<A> {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const last = useRef<A | null>(null)
  // A second click while the first request is out would start the same job
  // twice and the server would answer 409 "already running".
  const inFlight = useRef(false)
  const fnRef = useLatest(fn)

  const run = useCallback(async (...args: A) => {
    if (inFlight.current) return
    inFlight.current = true
    last.current = args
    setBusy(true)
    setError(null)
    try {
      await fnRef.current(...args)
    } catch (e) {
      setError(e)
    } finally {
      inFlight.current = false
      setBusy(false)
    }
  }, [])
  const retry = useCallback(() => {
    if (last.current) void run(...last.current)
  }, [run])
  const clearError = useCallback(() => setError(null), [])
  return { run, retry, busy, error, clearError }
}
