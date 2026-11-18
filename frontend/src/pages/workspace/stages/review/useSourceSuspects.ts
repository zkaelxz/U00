import { useEffect, useState } from 'react'

import { listMisheard } from '../../../../api/misheard'
import { listSourceSuspects } from '../../../../api/sourceSuspect'
import type { MisheardSuggestion } from '../../../../types/misheard'
import type { SourceSuspectItem } from '../../../../types/sourceSuspect'

export interface SourceSuspects {
  items: SourceSuspectItem[]
  misheard: MisheardSuggestion[]
  count: number
}

const NONE: SourceSuspects = { items: [], misheard: [], count: 0 }

// The lines flagged "Source text looks off" and the words that may be mis-heard, refetched
// whenever the lines change. A failed read of either leaves just that group out: both are a
// convenience over what the line rows already show.
export function useSourceSuspects(dramaId: number, reloads: number): SourceSuspects {
  const [found, setFound] = useState<SourceSuspects>(NONE)
  useEffect(() => {
    let live = true
    // Separate reads: the slower whole-title scan must not hold back the source-suspect chip.
    listSourceSuspects(dramaId).then(
      (r) => live && setFound((f) => ({ ...f, items: r.items, count: r.items.length + f.misheard.length })),
      () => live && setFound((f) => ({ ...f, items: [], count: f.misheard.length })),
    )
    listMisheard(dramaId).then(
      (r) => live && setFound((f) => ({ ...f, misheard: r.suggestions, count: f.items.length + r.suggestions.length })),
      () => live && setFound((f) => ({ ...f, misheard: [], count: f.items.length })),
    )
    return () => {
      live = false
    }
  }, [dramaId, reloads])
  return found
}
