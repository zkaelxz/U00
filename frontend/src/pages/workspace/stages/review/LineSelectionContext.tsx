import type { ReactNode } from 'react'

import { LineSelectionCtx } from './useLineSelectionContext'
import { useLineSelection } from './useLineSelection'

// Wraps the Review stage, so the lines list and anything beside it (the
// selection bar's actions, a dialog opened from one) read one selection.
// `titleId` resets it when the stage moves to another title, and a change of
// `reshapes` (a write that split, merged or replaced lines) clears it.
export function LineSelectionProvider({ titleId, reshapes = 0, children }: { titleId: number; reshapes?: number; children: ReactNode }) {
  return <LineSelectionCtx.Provider value={useLineSelection(titleId, reshapes)}>{children}</LineSelectionCtx.Provider>
}
