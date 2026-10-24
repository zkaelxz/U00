/*
 * The delayed player's decisions, kept apart from the component so they can be
 * tested without a video element: where to seek, what to do about a player
 * error, and when playback counts as stalled.
 */
import { apiUrl } from '../../api/client'

/**
 * How far (s) the measured delay may sit from its goal before the player seeks. The
 * newest recorded second moves in steps of one segment (4 s) as the playlist reloads,
 * so the measure swings by that much on its own; a tighter limit would seek forward
 * over footage every few seconds.
 */
export const SEEK_TOLERANCE_S = 6
/**
 * Seconds the recording's newest second trails the real live edge (the source's own
 * 2 s pieces and their fetch). The captions are timed from real live, so the picture
 * is this much further behind than the player can see. Judged from a real session,
 * where 6 s showed captions a few seconds late; not measured.
 */
export const RECORDER_LAG_S = 2
/** Seconds after a seek during which another is not started (the player is still settling). */
export const SEEK_COOLDOWN_S = 5
/** Seconds without playback progress before the player is nudged, then rebuilt. */
export const STALL_NUDGE_S = 6
export const STALL_REBUILD_S = 20
/** Fatal network errors one player reloads through before it is started again from scratch. */
export const MAX_RELOADS = 3

export const PREPARING_NOTE = 'Preparing the delayed player…'
export const UNSUPPORTED_NOTE = "This browser can't play the delayed copy; use the stream's own player instead."
export const RECOVERING_NOTE = 'The picture stalled; recovering…'

export const playerSrc = (sessionId: string) => apiUrl(`/api/live/player/${sessionId}/master.m3u8`)

export interface Placement {
  /** Seconds behind the newest recorded second the picture is meant to stay. */
  goal: number | null
  /** Where to move the playhead now, or null. */
  to: number | null
}

/**
 * Where the playhead belongs so the picture sits behind the newest recorded second by
 * the wanted delay. The goal is fixed when the picture is first placed (or the delay
 * changes, or the playhead falls off the back of the window): with less recorded than
 * wanted it is however much there is, and it then stays, because playing on moves the
 * playhead and the newest second together. Chasing the wanted delay as the recording
 * grows would seek back over what was just played, again and again.
 */
export function placement(seekStart: number, seekEnd: number, current: number, delay: number, goal: number | null): Placement {
  if (!Number.isFinite(seekEnd) || !Number.isFinite(current) || seekEnd - seekStart < 1) return { goal, to: null }
  const fixed = goal === null || current < seekStart - 0.5
    ? Math.min(Math.max(0, delay), seekEnd - seekStart)
    : goal
  const off = Math.abs(seekEnd - current - fixed) > SEEK_TOLERANCE_S
  return { goal: fixed, to: off ? seekEnd - fixed : null }
}

/** Seconds the captions wait while the player reports no seekable range to measure the picture's delay from. */
export const pictureDelayWithoutWindow = (delay: number) => Math.max(0, Math.round(delay + RECORDER_LAG_S))

export type ErrorAction = 'ignore' | 'reload' | 'recover-media' | 'rebuild'

/** What to do about an hls.js error; only fatal ones need action. */
export function errorAction(fatal: boolean, type: string): ErrorAction {
  if (!fatal) return 'ignore'
  if (type === 'networkError') return 'reload'
  if (type === 'mediaError') return 'recover-media'
  return 'rebuild'
}

export type StallAction = 'none' | 'nudge' | 'rebuild'

/** `stalledS`: seconds the playhead has not moved while it should be playing. */
export function stallAction(stalledS: number): StallAction {
  if (stalledS >= STALL_REBUILD_S) return 'rebuild'
  return stalledS >= STALL_NUDGE_S ? 'nudge' : 'none'
}
