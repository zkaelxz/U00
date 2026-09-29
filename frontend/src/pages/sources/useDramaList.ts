import { useEffect, useState } from 'react'

import { api } from '../../api/client'
import type { DramaSummary } from '../../api/types'

/** The library's dramas, loaded once; `add` puts a just-created one in front. */
export function useDramaList() {
  const [items, setItems] = useState<DramaSummary[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  useEffect(() => {
    let live = true
    api.listDramas().then(
      (r) => live && setItems(r.items),
      (e: unknown) => live && setError(e),
    )
    return () => {
      live = false
    }
  }, [])
  const add = (d: DramaSummary) => setItems((cur) => [d, ...(cur ?? []).filter((x) => x.id !== d.id)])
  return { items, error, add }
}
