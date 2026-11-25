// Swipes on the Watch picture: a short sideways swipe seeks, in the direction of the seek bar under it.

export const SWIPE_SEEK_SECONDS = 10
/** Below this the finger only wobbled, or tapped: the player's own tap handling decides. */
const MIN_SWIPE_PX = 60
/** A swipe starting this close to a screen edge is the phone's own Back gesture. */
export const EDGE_PX = 24

/** Seconds to seek for a finger's travel, or null for a tap, a mostly vertical drag (the page scroll) or a wobble. */
export function swipeSeek(dx: number, dy: number): number | null {
  if (Math.abs(dx) < MIN_SWIPE_PX || Math.abs(dy) > Math.abs(dx) / 2) return null
  return dx > 0 ? SWIPE_SEEK_SECONDS : -SWIPE_SEEK_SECONDS
}

/** True when a touch begins in the strip along either edge of a screen `width` wide. */
export const startsAtEdge = (x: number, width: number) => x < EDGE_PX || x > width - EDGE_PX
