// Owns the Set speaker panel's open flag for the panel that holds the line selection.
import { useState, type ReactNode } from 'react'

import { SetSpeakerPanel } from './SetSpeakerPanel'

// open is for the selection bar, node goes in the page.
export function useSetSpeakerPanel(dramaId: number, lineIds: number[], onChanged: () => void): { open: () => void; node: ReactNode } {
  // 0 is closed; each press of the bar's button bumps it, so a panel that is already open scrolls back into view.
  const [openSignal, setOpenSignal] = useState(0)
  const node = openSignal > 0 && lineIds.length > 0
    ? <SetSpeakerPanel dramaId={dramaId} lineIds={lineIds} openSignal={openSignal} onChanged={onChanged} onClose={() => setOpenSignal(0)} />
    : null
  return { open: () => setOpenSignal((n) => n + 1), node }
}
