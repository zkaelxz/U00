import { useState } from 'react'
import { useCancellableEffect } from './useCancellableEffect'

export type Loaded<T> = { data: T | null; error: unknown }

// Loads once per reloadKey; a failed panel shows its own banner instead of blanking the page.
export function useLoad<T>(load: () => Promise<T>, reloadKey: number): Loaded<T> {
  const [state, setState] = useState<Loaded<T>>({ data: null, error: null })
  useCancellableEffect((cancelled) => {
    load().then(
      (data) => !cancelled() && setState({ data, error: null }),
      (error: unknown) => !cancelled() && setState({ data: null, error }),
    )
    // load is a stable module-level function.
  }, [load, reloadKey])
  return state
}
