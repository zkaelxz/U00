import { useEffect, useState } from 'react'

import { listMisheard } from '../../../../api/misheard'
import { listSourceSuspects } from '../../../../api/sourceSuspect'
import type { MisheardSuggestion } from '../../../../types/misheard'
import type { SourceSuspectItem } from '../../../../types/sourceSuspect'

export interface SourceSuspects {
  items: SourceSuspectItem[]
  misheard: { items: MisheardSuggestion[]; kept: number }
  count: number
}

// A title whose suggestions were all kept still counts one, so the chip stays and "Show kept words
// again" can be reached.
const tally = (lines: number, m: SourceSuspects['misheard']) => lines + m.items.length + (m.items.length === 0 && m.kept > 0 ? 1 : 0)

const NONE: SourceSuspects = { items: [], misheard: { items: [], kept: 0 }, count: 0 }

// The lines flagged "Source text looks off" and the words that may be mis-heard, refetched
// whenever the lines change. A failed read of either leaves just that group out: both are a
// convenience over what the line rows already show.
export function useSourceSuspects(dramaId: number, reloads: number): SourceSuspects {
  const [found, setFound] = useState<SourceSuspects>(NONE)
  useEffect(() => {
    let live = true
    // Separate reads: the slower whole-title scan must not hold back the source-suspect chip.
    listSourceSuspects(dramaId).then(
      (r) => live && setFound((f) => ({ ...f, items: r.items, count: tally(r.items.length, f.misheard) })),
      () => live && setFound((f) => ({ ...f, items: [], count: tally(0, f.misheard) })),
    )
    listMisheard(dramaId).then(
      (r) => live && setFound((f) => ({ ...f, misheard: { items: r.suggestions, kept: r.kept_count }, count: tally(f.items.length, { items: r.suggestions, kept: r.kept_count }) })),
      () => live && setFound((f) => ({ ...f, misheard: { items: [], kept: 0 }, count: tally(f.items.length, { items: [], kept: 0 }) })),
    )
    return () => {
      live = false
    }
  }, [dramaId, reloads])
  return found
}
