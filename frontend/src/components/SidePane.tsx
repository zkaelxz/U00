// SidePane: a page's sections as a list on the left (Tabs with `side`) and the one on show on the right.
import type { ReactNode } from 'react'
import './tabs.css'

export function SidePane({ nav, children }: { nav: ReactNode; children: ReactNode }) {
  return (
    <div className="ui-sidepane">
      <div className="ui-sidepane-nav">{nav}</div>
      <div className="ui-sidepane-body">{children}</div>
    </div>
  )
}
