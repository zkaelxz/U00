/*
 * Accordion bookkeeping for Section's `group` prop: sections that share a group
 * id are exclusive. announceOpen tells every other member to close.
 */
type Listener = { group: string; id: string; close: () => void }

const listeners = new Set<Listener>()

/** Register a section; returns the unsubscribe. `close` runs when another member opens. */
export function joinGroup(group: string, id: string, close: () => void): () => void {
  const l: Listener = { group, id, close }
  listeners.add(l)
  return () => {
    listeners.delete(l)
  }
}

/** The section `id` opened: close every other member of `group`. */
export function announceOpen(group: string, id: string): void {
  for (const l of [...listeners]) {
    if (l.group === group && l.id !== id) l.close()
  }
}
