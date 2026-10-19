/*
 * Followed channels on the Live page (api/routers/live_follow_routes.py).
 * Listing is `library.read`; adding, changing, checking, removing and Start
 * Live need `media.import_url` (Start Live with a paid engine also
 * `engines.paid`). "Start Live automatically" is the PC owner's only; the
 * list says whether this viewer may offer it (`can_auto_start`). Responses
 * never carry a URL.
 */
import type {
  LiveFollow,
  LiveFollowAdd,
  LiveFollowList,
  LiveFollowOptions,
  LiveFollowUpdate,
  LiveSessionStart,
  LiveSessionStarted,
} from '../types/live'
import { ApiError, getJson, postJson, type Fetch } from './client'
import { describeError } from '../components/errorMessages'

const BASE = '/api/live/follows'
// LiveFollowAdd.channel in api/schemas/transcribe.py.
export const MAX_CHANNEL_INPUT = 300
const HOSTS = ['youtube.com', 'www.youtube.com', 'm.youtube.com']

const followPath = (id: number) => {
  if (!Number.isInteger(id) || id < 1) throw new ApiError(404, { code: 'not_found', message: 'No such followed channel.' })
  return `${BASE}/${id}`
}

export const listFollows = (f?: Fetch) => getJson<LiveFollowList>(BASE, f)
export const addFollow = (body: LiveFollowAdd, f?: Fetch) => postJson<LiveFollow>(BASE, body, f)
export const updateFollow = async (id: number, body: LiveFollowUpdate, f?: Fetch) =>
  postJson<LiveFollow>(followPath(id), body, f)
export const removeFollow = async (id: number, f?: Fetch) =>
  postJson<{ id: number; removed: boolean }>(`${followPath(id)}/remove`, undefined, f)
export const checkFollow = async (id: number, f?: Fetch) =>
  postJson<{ id: number; queued: boolean }>(`${followPath(id)}/check`, undefined, f)
export const checkAllFollows = (f?: Fetch) =>
  postJson<{ queued: number; skipped: number }>(`${BASE}/check-all`, undefined, f)
export const startFromFollow = async (id: number, f?: Fetch) =>
  postJson<LiveSessionStarted>(`${followPath(id)}/start`, undefined, f)

/** The options a follow keeps, from a Live start body (whole minutes, as the server takes them). */
export function followOptions(b: LiveSessionStart): LiveFollowOptions {
  return {
    source_language: b.source_language,
    whisper_size: b.whisper_size,
    engine: b.engine ?? null,
    model: b.model ?? null,
    max_minutes: Math.round(b.max_minutes),
    use_gpu: b.use_gpu,
    glossary_drama_id: b.glossary_drama_id ?? null,
    series_id: b.series_id ?? null,
  }
}

/** One line naming a follow's saved Live options. */
export function optionsSummary(o: LiveFollowOptions): string {
  return `${o.source_language} · ${o.engine || 'Ollama'}${o.model ? ` ${o.model}` : ''} · Whisper ${o.whisper_size} · ${o.use_gpu ? 'GPU' : 'CPU'} · stop after ${o.max_minutes} min`
}

/** Why Follow can't be pressed with this text yet, or null. The server checks again. */
export function checkChannelInput(text: string): string | null {
  const t = text.trim()
  if (!t) return 'Still needed: a channel link or @handle.'
  if (t.length > MAX_CHANNEL_INPUT) return 'That link is too long.'
  if (t.startsWith('@')) return t.length >= 4 ? null : 'A handle has at least 3 characters after the @.'
  let parsed: URL
  try {
    parsed = new URL(t.includes('://') ? t : `https://${t}`)
  } catch {
    return 'Paste a YouTube channel link or an @handle.'
  }
  if (!HOSTS.includes(parsed.hostname.toLowerCase())) return 'Only YouTube channels can be followed for now.'
  return null
}

/** "Live now: <title>", "Starts at 14:05", "Not live", the error words, or "Not checked yet". */
export function followStatusText(f: Pick<LiveFollow, 'state' | 'last_status' | 'starts_at' | 'last_checked_at'> & { stream_title?: string }): string {
  if (f.last_checked_at === null && !f.state) return 'Not checked yet'
  if (f.state === 'live' && f.stream_title) return `Live now: ${f.stream_title}`
  if (f.state === 'upcoming' && typeof f.starts_at === 'number' && Number.isFinite(f.starts_at)) {
    const at = new Date(f.starts_at * 1000).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
    return f.stream_title ? `Starts at ${at}: ${f.stream_title}` : `Starts at ${at}`
  }
  return f.last_status || 'Not checked yet'
}

/** Why "Start Live automatically" can't be picked for this channel, or null. */
export function autoStartBlocker(f: Pick<LiveFollow, 'auto_check'>): string | null {
  return f.auto_check ? null : 'Turn on Check every hour first: only an automatic check can notice the stream and start Live.'
}

/** "Checked 5 min ago" / "Not checked yet". */
export function checkedText(at: number | null, nowSec: number = Date.now() / 1000): string {
  if (typeof at !== 'number') return 'Not checked yet'
  const s = Math.max(0, nowSec - at)
  if (s < 60) return 'Checked just now'
  if (s < 3600) return `Checked ${Math.floor(s / 60)} min ago`
  if (s < 86_400) return `Checked ${Math.floor(s / 3600)} h ago`
  return `Checked ${Math.floor(s / 86_400)} d ago`
}

export const FOLLOW_FORBIDDEN =
  "This account can't do that. Following channels needs the “import from a link” permission, and a paid engine in a channel's options also needs “paid engines”. Ask the owner of the main PC."

/** Plain text for a failed follow request. */
export function describeFollowError(err: unknown): string {
  const e = err as Partial<ApiError> | null
  if (e?.status === 403) return FOLLOW_FORBIDDEN
  if (e?.status === 429) return 'This channel was checked less than a minute ago. Try again in a minute.'
  const { title, detail } = describeError(err, { serverText: true })
  return detail ? `${title} ${detail}` : title
}

// The Live page listens for this to show a session started from a followed
// channel (the card's or the bell's Start Live) without a reload.
export const LIVE_STARTED_EVENT = 'baihe:live-started'

export function announceLiveStarted(sessionId: string): void {
  try {
    window.dispatchEvent(new CustomEvent(LIVE_STARTED_EVENT, { detail: sessionId }))
  } catch {
    // No window (tests without a DOM): nothing is listening anyway.
  }
}
