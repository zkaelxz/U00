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
      return `${YT_ORIGIN}/embed/${ref.id}?enablejsapi=1&playsinline=1`
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
}

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
  return { currentTime: num(info.currentTime), duration: num(info.duration), ...(isLive === undefined ? {} : { isLive }) }
}

export type DelayPlan = { kind: 'seek'; to: number } | { kind: 'wait' } | { kind: 'unsupported' }

/**
 * Where to play so the picture is `delay` seconds behind the live edge.
 * `duration` is the end of the seekable window (0 or missing when the stream
 * keeps no rewind buffer). Waits for a duration until `waitedS` passes
 * DVR_WAIT_S, then reports that the stream can't be delayed. With less
 * rewind buffer than asked, it plays as far back as the buffer allows.
 */
export function planDelay(info: PlayerInfo | null, delay: number, waitedS: number): DelayPlan {
  // A finished video has a duration too; seeking to its end would skip the show.
  if (info?.isLive === false) return { kind: 'unsupported' }
  const d = info?.duration
  if (d === undefined || d <= 1) return waitedS >= DVR_WAIT_S ? { kind: 'unsupported' } : { kind: 'wait' }
  const wanted = Math.max(0, Math.min(DELAY_RANGE[1], delay))
  return { kind: 'seek', to: Math.max(0, Math.floor(d - wanted)) }
}

/** The postMessage payloads for the YouTube player. */
export const ytListenMessage = () => JSON.stringify({ event: 'listening', id: 1, channel: 'widget' })
export const ytSeekMessage = (to: number) => JSON.stringify({ event: 'command', func: 'seekTo', args: [to, true] })
