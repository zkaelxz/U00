import { useEffect, useState } from 'react'

import { listSourceSuspects } from '../../../../api/sourceSuspect'
import type { SourceSuspectItem } from '../../../../types/sourceSuspect'

// The lines flagged "Source text looks off", refetched whenever the lines change.
// A failed read leaves the chip hidden: it is a convenience over the flag, which
// the line rows still show.
export function useSourceSuspects(dramaId: number, reloads: number): SourceSuspectItem[] {
  const [items, setItems] = useState<SourceSuspectItem[]>([])
  useEffect(() => {
    let live = true
    listSourceSuspects(dramaId).then(
      (r) => live && setItems(r.items),
      () => live && setItems([]),
    )
    return () => {
      live = false
    }
  }, [dramaId, reloads])
  return items
}
