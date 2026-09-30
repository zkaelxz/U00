import { useEffect, useState } from 'react'

import { getImportState } from '../../api/sourcesImport'
import type { ImportState } from '../../types/sourcesImport'

/**
 * Step 107: the chosen drama's import state for one series (GET
 * /api/sources/{name}/import-state): chapters already imported, and the
 * ones earlier runs left failed or not attempted. Loads when a drama is
 * chosen and again each time an import stops running (`running` goes
 * false). Nothing loads without a drama or while `enabled` is false.
 */
export function useImportState(source: string, seriesId: string, dramaId: number | null, enabled: boolean, running: boolean) {
  const key = enabled && dramaId ? `${source}\n${seriesId}\n${dramaId}` : null
  const [loaded, setLoaded] = useState<{ key: string; state: ImportState } | null>(null)
  const [failed, setFailed] = useState<{ key: string; error: unknown } | null>(null)
  useEffect(() => {
    if (!key || !dramaId || running) return
    let live = true
    getImportState(source, seriesId, dramaId).then(
      (state) => {
        if (!live) return
        setLoaded({ key, state })
        setFailed(null)
      },
      (error: unknown) => {
        if (!live) return
        // Don't keep showing marks and a retry set from before the last run.
        setLoaded(null)
        setFailed({ key, error })
      },
    )
    return () => {
      live = false
    }
  }, [key, source, seriesId, dramaId, running])
  // Only this series + drama's answer counts (the drama or series may have changed since).
  return {
    state: loaded && loaded.key === key ? loaded.state : null,
    error: failed && failed.key === key ? failed.error : null,
  }
}
