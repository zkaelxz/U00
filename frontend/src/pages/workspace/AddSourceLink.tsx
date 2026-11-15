import type { MouseEvent } from 'react'

import { routeHref } from '../../router'

/** The Details field the link leads to. */
export const SOURCE_URL_FIELD_ID = 'details-source-url'
/** Raised when the link is pressed on this title's Media stage, where a click may change nothing the page can see. */
export const ADD_SOURCE_LINK_EVENT = 'baihe:add-source-link'

/** A title without a source link offers this in its header: Details opens at the link field, ready to type in. */
export function AddSourceLink({ id }: { id: number }) {
  const href = routeHref({ name: 'drama', id, stage: 'source', tab: 'details', focus: 'sourceLink' })
  // On the Media stage the address may already hold the link's focus (or equal it), so the route does not change and
  // would not bring the field back from a folded card, a scroll or another tab; the panel is asked directly instead.
  const onClick = (e: MouseEvent) => {
    if (!window.location.hash.startsWith(`#/drama/${id}/source`)) return
    e.preventDefault()
    if (window.location.hash !== href) window.location.hash = href
    window.dispatchEvent(new Event(ADD_SOURCE_LINK_EVENT))
  }
  return <a className="ws-source-add muted" href={href} onClick={onClick}>Add a source link</a>
}
