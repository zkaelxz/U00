import { useEffect, type DependencyList } from 'react'

// Runs an async load whose result must be dropped once the effect re-runs or
// the component unmounts; `cancelled()` is the check to make before setting state.
export function useCancellableEffect(run: (cancelled: () => boolean) => void, deps: DependencyList): void {
  useEffect(() => {
    let done = false
    run(() => done)
    return () => {
      done = true
    }
  }, deps)
}
