/*
 * The delayed player's decisions, kept apart from the component so they can be
 * tested without a video element: where to seek, what to do about a player
 * error, and when playback counts as stalled.
 */
import { apiUrl } from '../../api/client'

/** How far (s) the measured delay may sit from the wanted one before the player seeks. */
export const SEEK_TOLERANCE_S = 3
/** Seconds after a seek during which another is not started (the player is still settling). */
export const SEEK_COOLDOWN_S = 5
/** Seconds without playback progress before the player is nudged, then rebuilt. */
export const STALL_NUDGE_S = 6
export const STALL_REBUILD_S = 20

export const PREPARING_NOTE = 'Preparing the delayed player…'
export const UNSUPPORTED_NOTE = "This browser can't play the delayed copy; use the stream's own player instead."
export const RECOVERING_NOTE = 'The picture stalled; recovering…'

export const playerSrc = (sessionId: string) => apiUrl(`/api/live/sessions/${sessionId}/player/master.m3u8`)

/**
 * Where to move the playhead so the picture is `delay` seconds behind the newest
 * recorded second, or null when it already is (within tolerance) or there is
 * nothing to measure. With less recorded than asked it plays from the oldest second.
 */
export function planSeek(seekStart: number, seekEnd: number, current: number, delay: number): number | null {
  if (!Number.isFinite(seekEnd) || !Number.isFinite(current) || seekEnd - seekStart < 1) return null
  const target = Math.max(seekStart, seekEnd - Math.max(0, delay))
  const measured = seekEnd - current
  const reachable = Math.min(Math.max(0, delay), seekEnd - seekStart)
  return Math.abs(measured - reachable) > SEEK_TOLERANCE_S ? target : null
}

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
