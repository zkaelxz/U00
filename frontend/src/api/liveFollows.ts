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
import { BALANCED_CAPTIONS } from './live'
import { describeError } from '../components/errorMessages'

const BASE = '/api/live/follows'
// LiveFollowAdd.channel in api/schemas/transcribe.py.
export const MAX_CHANNEL_INPUT = 300
const HOSTS = ['youtube.com', 'www.youtube.com', 'm.youtube.com', 'twitch.tv', 'www.twitch.tv', 'm.twitch.tv']

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

// The server's default session length, for a start body that leaves it out.
const DEFAULT_FOLLOW_MAX_MINUTES = 60

/** The options a follow keeps, from a Live start body (whole minutes, as the server takes them). */
export function followOptions(b: LiveSessionStart): LiveFollowOptions {
  return {
    source_language: b.source_language,
    whisper_size: b.whisper_size,
    engine: b.engine ?? null,
    model: b.model ?? null,
    max_minutes: b.max_minutes === null ? null : Math.round(b.max_minutes ?? DEFAULT_FOLLOW_MAX_MINUTES),
    use_gpu: b.use_gpu,
    glossary_drama_id: b.glossary_drama_id ?? null,
    series_id: b.series_id ?? null,
    captions: b.segment_seconds === BALANCED_CAPTIONS.segment_seconds && b.overlap_seconds === BALANCED_CAPTIONS.overlap_seconds
      ? 'balanced' : null,
  }
}

// api/schemas/transcribe.py LiveFollowTerm and LiveFollowOptions.terms.
export const MAX_FOLLOW_TERMS = 50

/** A channel's word list as the box shows it: one "original = translation" per line. */
export function termsToText(terms: LiveFollowOptions['terms']): string {
  return (terms ?? []).map((t) => `${t.original} = ${t.translation}`).join('\n')
}

/** The box's lines as a word list, or why it can't be saved. Blank lines are skipped; the server checks again. */
export function parseTerms(text: string): { terms: NonNullable<LiveFollowOptions['terms']>; error: string | null } {
  const terms: NonNullable<LiveFollowOptions['terms']> = []
  for (const [i, raw] of text.split('\n').entries()) {
    const line = raw.trim()
    if (!line) continue
    const cut = line.search(/[=＝]/)
    const original = cut < 0 ? '' : line.slice(0, cut).trim()
    const translation = cut < 0 ? '' : line.slice(cut + 1).trim()
    if (!original || !translation) return { terms, error: `Line ${i + 1}: write it as original = translation.` }
    if (original.length > 30) return { terms, error: `Line ${i + 1}: the original can be up to 30 characters.` }
    if (translation.length > 40) return { terms, error: `Line ${i + 1}: the translation can be up to 40 characters.` }
    terms.push({ original, translation })
  }
  if (terms.length > MAX_FOLLOW_TERMS) return { terms, error: `Up to ${MAX_FOLLOW_TERMS} words per channel.` }
  return { terms, error: null }
}

/**
 * The channel's own page, for opening it in a browser (in the phone app, Grab). The follow carries only
 * a short label ("@name", "channel/UC...", "twitch/name"), so the address is rebuilt from it; null for
 * a follow whose stored address is no longer valid.
 */
export function channelPageUrl(label: string): string | null {
  if (!label || label === 'invalid') return null
  if (label.startsWith('twitch/')) return `https://www.twitch.tv/${encodeURIComponent(label.slice('twitch/'.length))}`
  return `https://www.youtube.com/${label.split('/').map((part) => encodeURIComponent(part).replace('%40', '@')).join('/')}`
}

/** One line naming a follow's saved Live options. */
export function optionsSummary(o: LiveFollowOptions): string {
  return `${o.source_language} · ${o.engine || 'Ollama'}${o.model ? ` ${o.model}` : ''} · Whisper ${o.whisper_size} · ${o.use_gpu ? 'GPU' : 'CPU'}${o.captions === 'balanced' ? ' · balanced captions' : ''} · ${o.max_minutes === null ? 'no time limit' : `stop after ${o.max_minutes} min`}`
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
    return 'Paste a YouTube or Twitch channel link or an @handle.'
  }
  if (!HOSTS.includes(parsed.hostname.toLowerCase())) return 'Only YouTube and Twitch channels can be followed.'
  return null
}

// Channels one paste may follow, so a stray paste of a whole page can't queue hundreds of adds.
export const MAX_CHANNELS_PER_ADD = 50

/** The channels in the box: one per line (spaces and commas also separate them), duplicates dropped. */
export function splitChannels(text: string): string[] {
  return [...new Set(text.split(/[\s,]+/).filter(Boolean))]
}

/** Why Follow can't be pressed with this box yet, or null. Names the first bad line. */
export function checkChannelsInput(text: string): string | null {
  const channels = splitChannels(text)
  if (!channels.length) return checkChannelInput('')
  if (channels.length > MAX_CHANNELS_PER_ADD) return `Up to ${MAX_CHANNELS_PER_ADD} channels at a time.`
  for (const c of channels) {
    const why = checkChannelInput(c)
    if (why) return channels.length > 1 ? `${c}: ${why}` : why
  }
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

const FOLLOW_FORBIDDEN =
  "This account can't do that. Following channels needs the “import from a link” permission, and a paid engine in a channel's options also needs “paid engines”. Ask the PC owner."

/** Plain text for a failed follow request. */
export function describeFollowError(err: unknown): string {
  const e = err as Partial<ApiError> | null
  if (e?.status === 403) return FOLLOW_FORBIDDEN
  // The server's one short sentence ("Channel has already been added."), not the generic conflict title and detail.
  if (e?.status === 409 && e.message) return e.message
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

/** What a channel is called on screen: the account's own name for it, else the site's name, else its handle. */
export function followName(f: Pick<LiveFollow, 'nickname' | 'channel_name' | 'channel'>): string {
  return f.nickname || f.channel_name || f.channel
}

/** The site's own name for a channel that has a nickname, shown small beside it; null when the name is shown as it is. */
export function followOriginalName(f: Pick<LiveFollow, 'nickname' | 'channel_name' | 'channel'>): string | null {
  return f.nickname ? f.channel_name || f.channel : null
}
