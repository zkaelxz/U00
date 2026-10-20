import { useMemo, useState } from 'react'

import { getStrongerSuggestions } from '../../../../api/strongerEngine'
import type { StrongerEngineSuggestions } from '../../../../types/strongerEngine'
import { buildOffers, type StrongerOffer } from './strongerEngineLogic'
import { useCancellableEffect } from '../../../../hooks/useCancellableEffect'

// Which lines to offer the stronger engine for, by line id. Fetched
// once per drama and again after any write (`reloads`); the GET makes no
// engine call. Optional: a failure (or no permission) just offers none.
export function useStrongerOffers(dramaId: number, reloads: number): Map<number, StrongerOffer> {
  const [data, setData] = useState<StrongerEngineSuggestions | null>(null)
  useCancellableEffect((cancelled) => {
    getStrongerSuggestions(dramaId).then(
      (s) => !cancelled() && setData(s.drama_id === dramaId ? s : null),
      () => !cancelled() && setData(null),
    )
  }, [dramaId, reloads])
  return useMemo(() => buildOffers(data && data.drama_id === dramaId ? data : null), [data, dramaId])
}
