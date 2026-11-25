// Swipes on the Watch picture: a short sideways swipe seeks, a long swipe to the left goes to the next episode.

export const SWIPE_SEEK_SECONDS = 10
/** Below this the finger only wobbled, or tapped: the player's own tap handling decides. */
const MIN_SWIPE_PX = 60
/** A swipe this share of the picture's width counts as "go to the next episode". */
const NEXT_SHARE = 0.55

export type SwipeAction = { kind: 'seek'; by: number } | { kind: 'next' }

/** What a finger's travel means, or null for a tap, a mostly vertical drag (the page scroll) or a wobble. */
export function swipeAction(dx: number, dy: number, width: number): SwipeAction | null {
  if (Math.abs(dx) < MIN_SWIPE_PX || Math.abs(dy) > Math.abs(dx) / 2) return null
  if (dx < 0 && width > 0 && -dx >= width * NEXT_SHARE) return { kind: 'next' }
  // Left drags the picture forward in time, like turning a page.
  return { kind: 'seek', by: dx < 0 ? SWIPE_SEEK_SECONDS : -SWIPE_SEEK_SECONDS }
}
