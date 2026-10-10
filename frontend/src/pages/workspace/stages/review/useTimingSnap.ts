import { useEffect, useMemo, useState } from 'react'

import { getTimingCheck } from '../../../../api/timingCheck'
import type { ReviewLine } from '../../../../types/review'
import type { TimingSuggestion } from '../../../../types/timingCheck'
import { suggestionsByLine } from './timingCheckLogic'

export const TIMING_FLAG = 'timing_drift'

// The saved snap suggestions for the shown lines, fetched only while one of
// them carries the timing flag so other titles' reviews make no extra call.
export function useTimingSnap(dramaId: number, shown: ReviewLine[], reloads: number) {
  const [list, setList] = useState<TimingSuggestion[]>([])
  const anyFlagged = shown.some((l) => l.flag === TIMING_FLAG)
  useEffect(() => {
    if (!anyFlagged) return
    let cancelled = false
    getTimingCheck(dramaId).then(
      (s) => !cancelled && setList(s.suggestions),
      () => !cancelled && setList([]),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, anyFlagged, reloads])
  return useMemo(() => suggestionsByLine(list), [list])
}
