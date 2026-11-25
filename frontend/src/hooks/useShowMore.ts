/** Page-size state for lists that grow in steps ("Show more"). */
import { useState } from 'react'

export const SHOW_MORE_STEP = 50

/** The page size is reset by giving the list a new `resetKey` (a filter or a new load), so a shorter list is not left
 *  showing a long page it never asked for. */
export function useShowMore(resetKey: string | number = '', step = SHOW_MORE_STEP) {
  const [state, setState] = useState({ key: resetKey, limit: step })
  const limit = state.key === resetKey ? state.limit : step
  return { limit, more: () => setState({ key: resetKey, limit: limit + step }) }
}
