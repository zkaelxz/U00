import type { ReactNode } from 'react'

import { routeHref } from '../../router'
import type { DiagnosticsCard } from './diagnosticsTabs'

/**
 * A link to another Diagnostics card. It has a real href for copy and open in
 * a new tab, but a click is handled here: the hash may already be that value
 * (the person opened the page that way, then changed tab), and then no
 * hashchange would fire.
 */
export function CardLink({ card, onGoTo, children }: { card: DiagnosticsCard; onGoTo: (card: DiagnosticsCard) => void; children: ReactNode }) {
  return (
    <a href={routeHref({ name: 'diagnostics', card })} onClick={(e) => {
      e.preventDefault()
      onGoTo(card)
    }}>
      {children}
    </a>
  )
}
