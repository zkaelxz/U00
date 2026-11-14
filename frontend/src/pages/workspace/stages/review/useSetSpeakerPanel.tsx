// Owns the Set speaker panel's open flag for the panel that holds the line selection.
import { useState, type ReactNode } from 'react'

import { SetSpeakerPanel } from './SetSpeakerPanel'

// open is for the selection bar, node goes in the page.
export function useSetSpeakerPanel(dramaId: number, lineIds: number[], onChanged: () => void): { open: () => void; node: ReactNode } {
  const [shown, setShown] = useState(false)
  // Each press of the bar's button scrolls the panel into view once, even when it is already open.
  // The panel is dropped while nothing is ticked; ticking a line again brings it back where it was,
  // so a press already scrolled to must not scroll again then.
  const [pressed, setPressed] = useState(0)
  const [scrolled, setScrolled] = useState(0)
  const node = shown && lineIds.length > 0
    ? <SetSpeakerPanel dramaId={dramaId} lineIds={lineIds} scrollSignal={pressed === scrolled ? 0 : pressed}
        onScrolled={() => setScrolled(pressed)} onChanged={onChanged} onClose={() => setShown(false)} />
    : null
  const open = () => {
    setShown(true)
    setPressed((n) => n + 1)
  }
  return { open, node }
}
