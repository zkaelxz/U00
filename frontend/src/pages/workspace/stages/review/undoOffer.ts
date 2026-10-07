import { useCallback, useSyncExternalStore } from 'react'

// One Undo offer for the whole Review stage: any structural change makes every
// earlier offer stale (the server would refuse it), so the panel that made the
// latest change owns the only offer and the others' disappear.

export type UndoOwner = 'lines' | 'merge-short' | 'resplit'

interface Offer {
  owner: UndoOwner
  dramaId: number
  value: unknown
}

let current: Offer | null = null
const listeners = new Set<() => void>()

function publish(next: Offer | null) {
  current = next
  listeners.forEach((l) => l())
}

function subscribe(l: () => void) {
  listeners.add(l)
  return () => {
    listeners.delete(l)
  }
}

/** Drops whatever offer is showing, in any panel (an edit or a change that can't be undone). */
export function retireUndoOffer(): void {
  if (current) publish(null)
}

/** Makes `value` the only offer: any other panel's goes away. */
export function offerUndo(owner: UndoOwner, dramaId: number, value: unknown): void {
  publish({ owner, dramaId, value })
}

/** Clears `owner`'s offer, leaving another panel's alone. */
export function dropUndo(owner: UndoOwner): void {
  if (current?.owner === owner) publish(null)
}

export function undoOfferFor<T>(offer: Offer | null, owner: UndoOwner, dramaId: number): T | null {
  return offer && offer.owner === owner && offer.dramaId === dramaId ? (offer.value as T) : null
}

export const currentUndoOffer = (): Offer | null => current

/** Like useState for one panel's offer. Setting a value replaces any other panel's offer;
 *  setting null clears only this panel's. */
export function useUndoOffer<T>(owner: UndoOwner, dramaId: number): [T | null, (v: T | null) => void] {
  const offer = useSyncExternalStore(subscribe, currentUndoOffer)
  const set = useCallback(
    (v: T | null) => (v !== null ? offerUndo(owner, dramaId, v) : dropUndo(owner)),
    [owner, dramaId],
  )
  return [undoOfferFor<T>(offer, owner, dramaId), set]
}
