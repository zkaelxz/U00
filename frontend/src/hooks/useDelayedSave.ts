import { useEffect, useRef } from 'react'
import { useLatest } from './useLatest'

/**
 * Runs `save(value)` once `value` has stayed put for `delayMs`. Leaving
 * (unmount) with a save still waiting runs it at once with `leaving` set, so
 * the caller can send it in a way that outlives the page; a null value saves nothing.
 * `value` must keep its identity between renders (memoise an object).
 */
export function useDelayedSave<T>(value: T | null, delayMs: number, save: (value: T, leaving: boolean) => void) {
  const saveRef = useLatest(save)
  const waiting = useRef<T | null>(null)
  useEffect(() => {
    if (value === null) return
    waiting.current = value
    const t = setTimeout(() => {
      waiting.current = null
      saveRef.current(value, false)
    }, delayMs)
    return () => clearTimeout(t)
  }, [value, delayMs])
  useEffect(
    () => () => {
      const v = waiting.current
      waiting.current = null
      if (v !== null) saveRef.current(v, true)
    },
    [],
  )
}
