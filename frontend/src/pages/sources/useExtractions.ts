import { useCallback, useEffect, useState } from 'react'

import { getPcMode, loadPcMode } from '../../api/pcOnly'
import { listExtractions } from '../../api/sourcesTools'
import type { SourceExtraction } from '../../types/sourcesTools'

export type Extractions = {
  rows: SourceExtraction[] | null
  error: unknown
  busy: boolean
  load: () => void
  clearError: () => void
}

export function useExtractions(): Extractions {
  const [rows, setRows] = useState<SourceExtraction[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    setBusy(true)
    setError(null)
    listExtractions(15)
      .then(setRows, setError)
      .finally(() => setBusy(false))
  }, [])

  // Like the rest of Manage sources: wait for /api/meta, so a remote
  // viewer never sends this admin request.
  useEffect(() => {
    let off = false
    void loadPcMode().then(() => {
      if (!off && getPcMode() !== 'remote') load()
    })
    return () => {
      off = true
    }
  }, [load])

  return { rows, error, busy, load, clearError: () => setError(null) }
}
