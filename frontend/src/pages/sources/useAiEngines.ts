import { useEffect, useState } from 'react'

import { getAiEngines } from '../../api/sourcesExtraction'
import type { AiEngines } from '../../types/sourcesExtraction'

/** The fallback's engine list, loaded once, when first `enabled` (the AI toggle is on). */
export function useAiEngines(enabled: boolean) {
  const [engines, setEngines] = useState<AiEngines | null>(null)
  const [error, setError] = useState<unknown>(null)
  useEffect(() => {
    if (!enabled || engines) return
    let live = true
    getAiEngines().then(
      (r) => live && setEngines(r),
      (e: unknown) => live && setError(e),
    )
    return () => {
      live = false
    }
  }, [enabled, engines])
  return { engines, error }
}
