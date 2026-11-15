import type { MouseEvent } from 'react'

import { routeHref } from '../../router'

/** The Details field the link leads to. */
export const SOURCE_URL_FIELD_ID = 'details-source-url'
/** Raised when the link is pressed on the address it points to, which a click alone would not change. */
export const ADD_SOURCE_LINK_EVENT = 'baihe:add-source-link'

/** A title without a source link offers this in its header: Details opens at the link field, ready to type in. */
export function AddSourceLink({ id }: { id: number }) {
  const href = routeHref({ name: 'drama', id, stage: 'source', tab: 'details', focus: 'sourceLink' })
  // Already on that address a click changes nothing, so the field would stay wherever it was left, folded or scrolled away.
  const onClick = (e: MouseEvent) => {
    if (window.location.hash !== href) return
    e.preventDefault()
    window.dispatchEvent(new Event(ADD_SOURCE_LINK_EVENT))
  }
  return <a className="ws-source-add muted" href={href} onClick={onClick}>Add a source link</a>
}
