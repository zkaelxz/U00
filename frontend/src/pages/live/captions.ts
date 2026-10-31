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
function timelineOrigin(cues: LiveCue[], arrivedAt: ReadonlyMap<number, number>): number | null {
  let origin: number | null = null
  for (const c of cues) {
    const at = arrivedAt.get(c.id)
    if (at === undefined) continue
    const o = at - c.start * 1000
    if (origin === null || o < origin) origin = o
  }
  return origin
}

/** A chunk's lines are paced over at most this much of their own timeline when they arrive late. */
const DEFAULT_REPLAY_SPAN_S = 20

/**
 * Wall-clock ms from which each cue may be shown when it arrived too late for its due
 * time: a chunk's lines all arrive at once, so with little picture delay they would
 * all be due together and only the last one drawn. They are paced from their arrival
 * as they were spoken instead, but only the lines of the batch's newest chunk (less than
 * `spanS` before its newest line): older ones, from a backlog or a reload that brings in
 * the whole feed, stay at the arrival so the newest chunk is not held back.
 */
function pacedStarts(cues: LiveCue[], arrivedAt: ReadonlyMap<number, number>, spanS: number): Map<number, number> {
  const last = new Map<number, number>()
  for (const c of cues) {
    const at = arrivedAt.get(c.id)
    if (at !== undefined) last.set(at, Math.max(last.get(at) ?? -Infinity, c.start))
  }
  // Only lines less than one chunk before the batch's newest can share its chunk; older
  // ones (an earlier chunk, or history) stay at the arrival and are replaced at once.
  const first = new Map<number, number>()
  const inChunk = (c: LiveCue, at: number) => c.start > (last.get(at) ?? 0) - Math.max(0, spanS)
  for (const c of cues) {
    const at = arrivedAt.get(c.id)
    if (at !== undefined && inChunk(c, at)) first.set(at, Math.min(first.get(at) ?? Infinity, c.start))
  }
  const out = new Map<number, number>()
  for (const c of cues) {
    const at = arrivedAt.get(c.id)
    if (at === undefined) continue
    out.set(c.id, inChunk(c, at) ? at + (c.start - (first.get(at) ?? c.start)) * 1000 : at)
  }
  return out
}

/** Wall-clock ms at which this cue's caption is due and when it clears. `pacedFrom`: see pacedStarts. */
function captionWindow(cue: LiveCue, origin: number, delayS: number, arrivedAt: number, pacedFrom = arrivedAt): [number, number] {
  // Never earlier than the cue's arrival: nothing can be shown before it is known.
  const from = Math.max(origin + (cue.start + delayS) * 1000, arrivedAt, pacedFrom)
  const hold = Math.min(HOLD_MAX_S, Math.max(HOLD_MIN_S, cue.end - cue.start + 1))
  return [from, from + hold * 1000]
}

/** The caption to draw now: the newest cue that is due and not yet cleared. */
export function captionAt(
  cues: LiveCue[], arrivedAt: ReadonlyMap<number, number>, delayS: number, now: number, spanS = DEFAULT_REPLAY_SPAN_S,
): Caption | null {
  const origin = timelineOrigin(cues, arrivedAt)
  if (origin === null) return null
  const paced = pacedStarts(cues, arrivedAt, spanS)
  for (let i = cues.length - 1; i >= 0; i--) {
    const c = cues[i]
    const at = arrivedAt.get(c.id)
    if (at === undefined) continue
    const [from, until] = captionWindow(c, origin, Math.max(0, delayS), at, paced.get(c.id))
    // A newer cue that is due replaces this one, so only the newest due is tried.
    if (now < from) continue
    if (now >= until) return null
    const translated = c.translation === 'done' && c.translated.trim() !== ''
    return { id: c.id, text: translated ? c.translated : c.text, pending: c.translation === 'pending' }
  }
  return null
}
