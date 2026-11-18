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
    Promise.all([
      listSourceSuspects(dramaId).then((r) => r.items, () => []),
      listMisheard(dramaId).then((r) => r.suggestions, () => []),
    ]).then(([items, misheard]) => {
      if (live) setFound({ items, misheard, count: items.length + misheard.length })
    })
    return () => {
      live = false
    }
  }, [dramaId, reloads])
  return found
}
