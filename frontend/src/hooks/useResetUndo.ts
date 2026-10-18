import { useCallback, useEffect, useRef, useState } from 'react'

export const RESET_UNDO_MS = 8000

/**
 * A reset that can be taken back for a few seconds. `reset` runs the reset and
 * keeps `restore` in memory only; nothing is stored, so a reload forgets it.
 */
export function useResetUndo() {
  const [restore, setRestore] = useState<(() => void) | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const clear = useCallback(() => {
    if (timer.current) clearTimeout(timer.current)
    timer.current = null
    setRestore(null)
  }, [])

  const reset = useCallback((apply: () => void, undo: () => void) => {
    apply()
    if (timer.current) clearTimeout(timer.current)
    setRestore(() => undo)
    timer.current = setTimeout(clear, RESET_UNDO_MS)
  }, [clear])

  const undo = useCallback(() => {
    restore?.()
    clear()
  }, [restore, clear])

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current) }, [])

  return { offered: restore !== null, reset, undo }
}
