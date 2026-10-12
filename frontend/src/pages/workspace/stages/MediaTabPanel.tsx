import { Children, type ReactNode } from 'react'

import { tabButtonId, tabPanelId } from '../../../components/tabIds'
import { MEDIA_TAB_PREFIX, type MediaTab } from '../mediaTabs'

// One sub-tab's body. Every panel stays mounted (hidden when not selected) so a half-filled form
// survives a tab switch.
export function MediaTabPanel({ id, active, children }: { id: MediaTab; active: MediaTab; children: ReactNode }) {
  const blank = Children.toArray(children).length === 0
  return (
    <div
      id={tabPanelId(MEDIA_TAB_PREFIX, id)}
      role="tabpanel"
      aria-labelledby={tabButtonId(MEDIA_TAB_PREFIX, id)}
      hidden={active !== id}
      className="source-panel media-tabpanel"
    >
      {blank ? <p className="muted" data-testid="media-tab-empty">Nothing to set up here for this title.</p> : children}
    </div>
  )
}
