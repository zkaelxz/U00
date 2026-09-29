/*
 * useAction: one Reader action (an AI tool, a lookup, a save) with its busy
 * state and error. The last call's arguments are remembered so "Try again"
 * (shown by ActionError after a 429) repeats it.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

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
  const fnRef = useRef(fn)
  useEffect(() => {
    fnRef.current = fn
  })

  const run = useCallback(async (...args: A) => {
    last.current = args
    setBusy(true)
    setError(null)
    try {
      await fnRef.current(...args)
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }, [])
  const retry = useCallback(() => {
    if (last.current) void run(...last.current)
  }, [run])
  const clearError = useCallback(() => setError(null), [])
  return { run, retry, busy, error, clearError }
}
