const KEY = 'baiheNavIdx'

let current = -1

/**
 * Numbers the browser's history entries as this app visits them (0 is the first page of the tab), so
 * "is there an earlier page of ours to go back to" does not depend on the Navigation API (Safari and
 * Firefox lack it). A new entry has no number yet; a back or forward lands on one that already has it.
 * Idempotent: hashchange and popstate both call it for the same navigation.
 */
export function stampNavEntry(): number {
  const state = (window.history.state ?? {}) as Record<string, unknown>
  const seen = state[KEY]
  if (typeof seen === 'number') {
    current = seen
    return current
  }
  current += 1
  window.history.replaceState({ ...state, [KEY]: current }, '')
  return current
}
