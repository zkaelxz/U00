// The Review stage's shared line-selection context and the hook that reads it.
import { createContext, useContext } from 'react'

import type { LineSelection } from './useLineSelection'

export const LineSelectionCtx = createContext<LineSelection | null>(null)

/**
 * The lines ticked in Review, from anywhere under the stage's provider:
 * `selectedIds` (line ids, ascending by line number), `count`, `clear()`.
 * Ids survive filter, search and page changes and are dropped when the title
 * changes or the lines are restructured (split, merge, add, delete).
 */
export function useLineSelectionContext(): LineSelection {
  const sel = useContext(LineSelectionCtx)
  if (!sel) throw new Error('useLineSelectionContext needs a LineSelectionProvider')
  return sel
}
