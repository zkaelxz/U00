import { Children, type ReactNode } from 'react'

import { tabButtonId, tabPanelId } from '../../../components/tabIds'
import { MEDIA_TAB_PREFIX } from '../mediaTabs'

// One sub-tab's body, for the Media and Translate stages (`prefix` is the Tabs idPrefix). Every panel stays
// mounted (hidden when not selected) so a half-filled form survives a tab switch.
export function MediaTabPanel({ id, active, shownOn = id, prefix = MEDIA_TAB_PREFIX, className = 'source-panel media-tabpanel', children }: {
  id: string
  active: string
  // The tab this panel is shown on, when it shares one with another panel (Tools under Transcribe).
  shownOn?: string
  prefix?: string
  className?: string
  children: ReactNode
}) {
  const blank = Children.toArray(children).length === 0
  return (
    <div
      id={tabPanelId(prefix, id)}
      role="tabpanel"
      aria-labelledby={tabButtonId(prefix, shownOn)}
      hidden={active !== shownOn}
      className={className}
    >
      {blank ? <p className="muted" data-testid="media-tab-empty">Nothing to set up here for this title.</p> : children}
    </div>
  )
}
