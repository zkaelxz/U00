import { useEffect, useState } from 'react'

export type Loaded<T> = { data: T | null; error: unknown }

// Loads once per reloadKey; a failed panel shows its own banner instead of blanking the page.
export function useLoad<T>(load: () => Promise<T>, reloadKey: number): Loaded<T> {
  const [state, setState] = useState<Loaded<T>>({ data: null, error: null })
  useEffect(() => {
    let cancelled = false
    load().then(
      (data) => !cancelled && setState({ data, error: null }),
      (error: unknown) => !cancelled && setState({ data: null, error }),
    )
    return () => {
      cancelled = true
    }
    // load is a stable module-level function.
  }, [load, reloadKey])
  return state
}
