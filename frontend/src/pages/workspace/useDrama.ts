import { useCallback, useEffect, useState } from 'react'

import { api, ApiError } from '../../api/client'
import type { DramaDetail } from '../../api/types'

interface Tagged {
  id: number
  drama?: DramaDetail
  error?: ApiError
}

// State is tagged with the drama id it was fetched for, so a slow response
// or leftover state from another drama is never shown for this one.
export function pickForId(state: Tagged | null, id: number): Tagged | null {
  return state && state.id === id ? state : null
}

export function useDrama(id: number) {
  const [state, setState] = useState<Tagged | null>(null)
  const [reloads, setReloads] = useState(0)

  useEffect(() => {
    let cancelled = false
    api.getDrama(id).then(
      (drama) => !cancelled && setState({ id, drama }),
      (e: unknown) => {
        if (cancelled) return
        const error =
          e instanceof ApiError
            ? e
            : new ApiError(0, { code: 'network_error', message: 'Lost contact with the API.' })
        setState((s) => ({ id, drama: pickForId(s, id)?.drama, error }))
      },
    )
    return () => {
      cancelled = true
    }
  }, [id, reloads])

  const refetch = useCallback(() => setReloads((n) => n + 1), [])
  const cur = pickForId(state, id)
  return { drama: cur?.drama ?? null, error: cur?.error ?? null, refetch }
}
