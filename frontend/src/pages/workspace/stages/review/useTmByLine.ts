import { useEffect, useMemo, useState } from 'react'

import { listTmSuggestions } from '../../../../api/review'
import type { ReviewLine, TmSuggestion } from '../../../../types/review'
import { useTmDismissed, visibleTm } from './tmDismiss'

// Translation-memory suggestions for the lines shown (R11), by line id.
// Optional: a failure (or no permission) just shows none.
export function useTmByLine(dramaId: number, shown: ReviewLine[], reloads: number) {
  const [tmList, setTmList] = useState<TmSuggestion[]>([])
  const tmDismissed = useTmDismissed(dramaId)
  const shownIds = shown.map((l) => l.id).join(',')
  useEffect(() => {
    let cancelled = false
    const ids = shownIds ? shownIds.split(',').map(Number).slice(0, 200) : []
    // Nothing shown: the old list matches no row, so it can stay.
    if (ids.length === 0) return
    listTmSuggestions(dramaId, undefined, ids).then(
      (list) => !cancelled && setTmList(list),
      () => !cancelled && setTmList([]),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, shownIds, reloads])
  return useMemo(() => {
    const m = new Map<number, TmSuggestion>()
    for (const s of visibleTm(tmList, tmDismissed)) if (s.line_id !== null) m.set(s.line_id, s)
    return m
  }, [tmList, tmDismissed])
}
