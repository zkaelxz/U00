// Owns the Set speaker panel's open flag for the panel that holds the line selection.
import { useState, type ReactNode } from 'react'

import { SetSpeakerPanel } from './SetSpeakerPanel'

// open is for the selection bar, node goes in the page.
export function useSetSpeakerPanel(dramaId: number, lineIds: number[], onChanged: () => void): { open: () => void; node: ReactNode } {
  const [shown, setShown] = useState(false)
  const node = shown && lineIds.length > 0
    ? <SetSpeakerPanel dramaId={dramaId} lineIds={lineIds} onChanged={onChanged} onClose={() => setShown(false)} />
    : null
  return { open: () => setShown(true), node }
}
