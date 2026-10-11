/*
 * Which line the "Captions over video" overlay shows, and when. Pure so the
 * timing is testable; LiveCaptions.tsx only feeds it the clock.
 *
 * A cue's start is seconds on the session's own timeline. The picture plays
 * `delay` seconds behind live, so the caption is due `delay` after the speech,
 * not when the cue happens to reach this browser. The timeline's place on the
 * wall clock is estimated from the cue that arrived with the least lag.
 */
import type { LiveCue } from '../../types/live'

/** A caption stays at least this long, so a short word can be read. */
export const HOLD_MIN_S = 3
/** ...and is cleared after this long even when the speaker went on. */
export const HOLD_MAX_S = 8

export interface Caption {
  id: number
  text: string
  /** True while only the transcript is known. */
  pending: boolean
}

/** Wall-clock ms at which the session timeline read 0, judged from when cues arrived. */
export function timelineOrigin(cues: LiveCue[], arrivedAt: ReadonlyMap<number, number>): number | null {
  let origin: number | null = null
  for (const c of cues) {
    const at = arrivedAt.get(c.id)
    if (at === undefined) continue
    const o = at - c.start * 1000
    if (origin === null || o < origin) origin = o
  }
  return origin
}

/** Wall-clock ms at which this cue's caption is due and when it clears. */
export function captionWindow(cue: LiveCue, origin: number, delayS: number, arrivedAt: number): [number, number] {
  // Never earlier than the cue's arrival: nothing can be shown before it is known.
  const from = Math.max(origin + (cue.start + delayS) * 1000, arrivedAt)
  const hold = Math.min(HOLD_MAX_S, Math.max(HOLD_MIN_S, cue.end - cue.start + 1))
  return [from, from + hold * 1000]
}

/** The caption to draw now: the newest cue that is due and not yet cleared. */
export function captionAt(
  cues: LiveCue[], arrivedAt: ReadonlyMap<number, number>, delayS: number, now: number,
): Caption | null {
  const origin = timelineOrigin(cues, arrivedAt)
  if (origin === null) return null
  for (let i = cues.length - 1; i >= 0; i--) {
    const c = cues[i]
    const at = arrivedAt.get(c.id)
    if (at === undefined) continue
    const [from, until] = captionWindow(c, origin, Math.max(0, delayS), at)
    // A newer cue that is due replaces this one, so only the newest due is tried.
    if (now < from) continue
    if (now >= until) return null
    const translated = c.translation === 'done' && c.translated.trim() !== ''
    return { id: c.id, text: translated ? c.translated : c.text, pending: c.translation === 'pending' }
  }
  return null
}
