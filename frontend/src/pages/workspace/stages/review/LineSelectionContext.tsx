import { createContext, useContext, type ReactNode } from 'react'

import { useLineSelection, type LineSelection } from './useLineSelection'

const Ctx = createContext<LineSelection | null>(null)

// Wraps the Review stage, so the lines list and anything beside it (the
// selection bar's actions, a dialog opened from one) read one selection.
// `titleId` resets it when the stage moves to another title.
export function LineSelectionProvider({ titleId, children }: { titleId: number; children: ReactNode }) {
  return <Ctx.Provider value={useLineSelection(titleId)}>{children}</Ctx.Provider>
}

/**
 * The lines ticked in Review, from anywhere under the stage's provider:
 * `selectedIds` (line ids, ascending by line number), `count`, `clear()`.
 * Ids survive filter, search and page changes and are dropped when the title
 * changes or the lines are restructured (split, merge, add, delete).
 */
export function useLineSelectionContext(): LineSelection {
  const sel = useContext(Ctx)
  if (!sel) throw new Error('useLineSelectionContext needs a LineSelectionProvider')
  return sel
}
