// Pure helpers for the Live page's optional stream video: which links can be
// shown, the iframe address built from validated parts only, and how far
// behind the live edge to play. No DOM here, so it is easy to swap for a
// server relay later.

export type StreamRef =
  | { kind: 'youtube'; id: string }
  | { kind: 'twitch-channel'; name: string }
  | { kind: 'twitch-video'; id: string }

export const YT_ORIGIN = 'https://www.youtube-nocookie.com'
export const DELAY_RANGE = [0, 90] as const
export const DEFAULT_DELAY = 15
/** Seconds to wait for the player to report a seekable window before saying it can't be delayed. */
export const DVR_WAIT_S = 10

const YT_ID = /^[A-Za-z0-9_-]{11}$/
const TWITCH_NAME = /^[A-Za-z0-9_]{1,25}$/
const TWITCH_VIDEO = /^[0-9]{1,15}$/
const YT_HOSTS = new Set(['youtube.com', 'www.youtube.com', 'm.youtube.com'])

/** The embeddable stream a pasted link points at, or null for anything else. */
export function parseStreamUrl(raw: string): StreamRef | null {
  let u: URL
  try {
    u = new URL(raw.trim())
  } catch {
    return null
  }
  if (u.protocol !== 'https:' && u.protocol !== 'http:') return null
  const host = u.hostname.toLowerCase()
  const parts = u.pathname.split('/').filter(Boolean)
  if (host === 'youtu.be') return ytRef(parts[0])
  if (YT_HOSTS.has(host)) {
    if (parts[0] === 'watch') return ytRef(u.searchParams.get('v'))
    if (parts[0] === 'live' || parts[0] === 'embed') return ytRef(parts[1])
    return null
  }
  if (host === 'twitch.tv' || host === 'www.twitch.tv' || host === 'm.twitch.tv') {
    if (parts[0] === 'videos' && parts[1] && TWITCH_VIDEO.test(parts[1])) return { kind: 'twitch-video', id: parts[1] }
    if (parts.length === 1 && TWITCH_NAME.test(parts[0])) return { kind: 'twitch-channel', name: parts[0] }
  }
  return null
}

function ytRef(id: string | null | undefined): StreamRef | null {
  return id && YT_ID.test(id) ? { kind: 'youtube', id } : null
}

/** The iframe address, built only from the validated id or channel. */
export function embedSrc(ref: StreamRef, parentHost: string): string {
  switch (ref.kind) {
    case 'youtube':
      return `${YT_ORIGIN}/embed/${ref.id}?enablejsapi=1&playsinline=1&autoplay=1`
    case 'twitch-channel':
      return `https://player.twitch.tv/?channel=${ref.name}&parent=${encodeURIComponent(parentHost)}`
    case 'twitch-video':
      return `https://player.twitch.tv/?video=v${ref.id}&parent=${encodeURIComponent(parentHost)}`
  }
}

/** Only YouTube's iframe API can be driven without loading a script. */
export const canDelay = (ref: StreamRef) => ref.kind === 'youtube'

export interface PlayerInfo {
  currentTime?: number
  duration?: number
  /** Reported by the player; undefined when it does not say. */
  isLive?: boolean
  /** YouTube's player state: -1 unstarted, 0 ended, 1 playing, 2 paused, 3 buffering, 5 cued. */
  playerState?: number
}

/**
 * True while the player has not started playing. It ignores seeks and reports
 * no seekable window then, so neither a seek nor "can't be delayed" is
 * decided yet. A player that never reports a state counts as started.
 */
export const notStarted = (info: PlayerInfo | null) => info?.playerState === -1 || info?.playerState === 5

/** What a YouTube player message says about playback, or null if it is not an infoDelivery. */
export function parseYouTubeInfo(data: unknown): PlayerInfo | null {
  let msg: unknown = data
  if (typeof data === 'string') {
    try {
      msg = JSON.parse(data)
    } catch {
      return null
    }
  }
  if (!msg || typeof msg !== 'object') return null
  const m = msg as { event?: unknown; info?: unknown }
  if (m.event !== 'infoDelivery' || !m.info || typeof m.info !== 'object') return null
  const info = m.info as Record<string, unknown>
  const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : undefined)
  const vd = info.videoData
  const isLive = vd && typeof vd === 'object' && typeof (vd as { isLive?: unknown }).isLive === 'boolean'
    ? (vd as { isLive: boolean }).isLive : undefined
  // Only fields the message carries: the player sends what changed, and an
  // absent field must not erase one remembered from an earlier message.
  const currentTime = num(info.currentTime)
  const duration = num(info.duration)
  const playerState = num(info.playerState)
  return {
    ...(currentTime === undefined ? {} : { currentTime }),
    ...(duration === undefined ? {} : { duration }),
    ...(isLive === undefined ? {} : { isLive }),
    ...(playerState === undefined ? {} : { playerState }),
  }
}

export type DelayPlan = { kind: 'seek'; to: number } | { kind: 'wait' } | { kind: 'unsupported' }

/**
 * Where to play so the picture is `delay` seconds behind the live edge.
 * `duration` is the end of the seekable window (0 or missing when the stream
 * keeps no rewind buffer). Waits for a duration until `waitedS` passes
 * DVR_WAIT_S, then reports that the stream can't be delayed. With less
 * rewind buffer than asked, it plays as far back as the buffer allows.
 * `offset` is how far `duration` sits past the playhead's own time base at the
 * live edge (see probeOffset); 0 when both count from the same point.
 */
export function planDelay(info: PlayerInfo | null, delay: number, waitedS: number, offset = 0): DelayPlan {
  // A finished video has a duration too; seeking to its end would skip the show.
  if (info?.isLive === false) return { kind: 'unsupported' }
  const d = info?.duration
  if (d === undefined || d <= 1) return waitedS >= DVR_WAIT_S ? { kind: 'unsupported' } : { kind: 'wait' }
  const wanted = Math.max(0, Math.min(DELAY_RANGE[1], delay))
  return { kind: 'seek', to: Math.max(0, Math.floor(d - offset - wanted)) }
}

export const NO_DELAY_NOTE = "This stream can't be delayed, so the picture runs ahead of the lines."
export const UNREACHABLE_NOTE = "The player isn't keeping a rewind buffer for this stream, so the picture can't be delayed and runs ahead of the lines."
export const WAITING_NOTE = 'Waiting for the player…'
export const NOT_STARTED_NOTE = 'Press play in the video; the delay is applied as soon as it starts.'
export const MUTED_NOTE = 'Started muted because the browser blocks sound until you interact with the video.'
/** Seconds the player may stay unstarted before it is started muted, which browsers allow without a gesture. */
export const AUTOPLAY_WAIT_S = 3
/** How far (s) the measured delay may sit from the wanted one and still count as reached. */
export const SETTLE_TOLERANCE_S = 3
/** A measured delay beyond this is not a delay but a sign that `duration` is not the live edge (the slider tops out far below it). */
export const IMPLAUSIBLE_DELAY_S = 300
/** How far (s) a probe seek must move the playhead to count as having reached the live edge. */
export const PROBE_MOVE_S = 5

/**
 * How far behind the live edge the player really is: the end of the seekable
 * window minus where it is playing, or null until it has reported both. Reads
 * the player, not the slider, so a seek it ignored shows.
 */
export function measuredDelay(info: PlayerInfo | null, offset = 0): number | null {
  const d = info?.duration
  const t = info?.currentTime
  if (d === undefined || t === undefined || d <= 1) return null
  return Math.max(0, Math.round(d - t - offset))
}

/**
 * For a live event YouTube documents `duration` as the time since the stream
 * began while `currentTime` counts from where playback started, so their
 * difference is not the distance from the live edge. After a seek past the end
 * (which lands at the live edge) the leftover difference is the constant
 * between the two clocks. Null when the playhead did not move, i.e. the seek
 * was ignored and the leftover tells nothing.
 */
export function probeOffset(timeBefore: number | undefined, info: PlayerInfo | null): number | null {
  const d = info?.duration
  const t = info?.currentTime
  if (timeBefore === undefined || d === undefined || t === undefined || d <= 1) return null
  if (Math.abs(t - timeBefore) < PROBE_MOVE_S) return null
  return Math.max(0, Math.round(d - t))
}

/** The most the player can rewind (the seekable window), or null before it reports one. */
function rewindLimit(info: PlayerInfo | null, offset: number): number | null {
  const d = info?.duration
  return d === undefined || d <= 1 ? null : Math.max(0, Math.floor(d - offset))
}

/** The delay a seek can reach: the wanted one, or the whole buffer when that is shorter. */
function reachableDelay(info: PlayerInfo | null, delay: number, offset: number): number {
  const wanted = Math.max(0, Math.min(DELAY_RANGE[1], delay))
  const limit = rewindLimit(info, offset)
  return limit === null ? wanted : Math.min(wanted, limit)
}

/** True once the measured delay is within tolerance of what a seek for `delay` can reach. */
export function delayReached(info: PlayerInfo | null, delay: number, offset = 0): boolean {
  const m = measuredDelay(info, offset)
  return m !== null && Math.abs(m - reachableDelay(info, delay, offset)) <= SETTLE_TOLERANCE_S
}

/**
 * The line under the video. `moving` is true between sending a seek and the
 * player confirming it, so the note answers the slider before the report.
 */
export function delayNote(info: PlayerInfo | null, delay: number, opts: { unsupported: boolean; moving: boolean; offset?: number }): string {
  if (notStarted(info)) return NOT_STARTED_NOTE
  if (opts.unsupported) return NO_DELAY_NOTE
  const offset = opts.offset ?? 0
  const m = measuredDelay(info, offset)
  if (m === null) return WAITING_NOTE
  if (opts.moving) return `Moving to about ${reachableDelay(info, delay, offset)} s behind live…`
  if (m > IMPLAUSIBLE_DELAY_S) return UNREACHABLE_NOTE
  const limit = rewindLimit(info, offset)
  const clamped = limit !== null && Math.max(0, Math.min(DELAY_RANGE[1], delay)) > limit
  return clamped
    ? `Playing about ${m} s behind live (this stream allows at most ${limit} s).`
    : `Playing about ${m} s behind live.`
}

/** The postMessage payloads for the YouTube player. */
export const ytListenMessage = () => JSON.stringify({ event: 'listening', id: 1, channel: 'widget' })
export const ytSeekMessage = (to: number) => JSON.stringify({ event: 'command', func: 'seekTo', args: [to, true] })
export const ytCommand = (func: string, args: unknown[] = []) => JSON.stringify({ event: 'command', func, args })
