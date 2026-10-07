/*
 * Live capture sessions (api/routers/live_routes.py, spec L-1). Status is also
 * pushed over GET /api/events (src/api/eventStream.ts); cues are read here.
 * Start needs `media.import_url` (plus `engines.paid` for a paid engine);
 * reading is `library.read`, stopping `jobs.cancel`. Sessions live in the
 * API process only: a 404 after a restart means the session is gone.
 */
import type { TranslateEngine } from '../types/translate'
import type {
  LiveCue,
  LiveSessionStart,
  LiveSessionStarted,
  LiveSessionStatus,
  LiveSessionStopped,
  LiveSessionSummary,
} from '../types/live'
import { ApiError, getJson, postJson } from './client'
import { describeError } from '../components/errorMessages'

type Fetch = typeof fetch

// Ranges and choices services/live_service.py accepts (it clamps numbers).
export const LIVE_LANGUAGES = [
  { code: 'zh', label: 'Chinese' },
  { code: 'ja', label: 'Japanese' },
  { code: 'ko', label: 'Korean' },
]
export const WHISPER_SIZES = ['tiny', 'base', 'small', 'medium']
export const SEGMENT_RANGE: [number, number] = [10, 60]
export const OVERLAP_RANGE: [number, number] = [0, 8]
export const MAX_MINUTES_RANGE: [number, number] = [1, 240]
export const MAX_URL_LEN = 2000
// The feed shows only the newest lines; the page keeps more
// in memory so a long session can't grow without bound.
const FEED_SHOWN = 50
const CUES_KEPT = 1000
export const POLL_MS = 2000

export interface LiveForm {
  url: string
  source_language: string
  engine: string
  // '' = the engine's own default model.
  model: string
  whisper_size: string
  segment_seconds: number
  overlap_seconds: number
  max_minutes: number
  use_gpu: boolean
}

export const DEFAULT_FORM: LiveForm = {
  url: '',
  source_language: 'zh',
  engine: '',
  model: '',
  whisper_size: 'small',
  segment_seconds: 20,
  overlap_seconds: 3,
  max_minutes: 60,
  use_gpu: false,
}

// The options remembered per browser (everything but the link).
export type LiveOptions = Omit<LiveForm, 'url'>
export const DEFAULT_OPTIONS: LiveOptions = (({ url: _url, ...rest }) => rest)(DEFAULT_FORM)

const SID = /^live_[0-9a-f]{32}$/
const sessionPath = (id: string) => {
  if (!SID.test(id)) throw new ApiError(404, { code: 'not_found', message: 'No such live session.' })
  return `/api/live/sessions/${id}`
}

export const startLive = (body: LiveSessionStart, f?: Fetch) =>
  postJson<LiveSessionStarted>('/api/live/sessions', body, f)
export const listLive = (f?: Fetch) => getJson<LiveSessionSummary[]>('/api/live/sessions', f)
export const getLive = async (id: string, after: number, f?: Fetch) =>
  getJson<LiveSessionStatus>(`${sessionPath(id)}?after=${Math.max(0, Math.floor(after))}`, f)
export const stopLive = async (id: string, f?: Fetch) => postJson<LiveSessionStopped>(`${sessionPath(id)}/stop`, undefined, f)

/** Why Start can't be pressed with this link yet, or null. */
export function checkLiveUrl(url: string): string | null {
  const u = url.trim()
  if (!u) return 'Still needed: a stream link.'
  if (u.length > MAX_URL_LEN) return 'That link is too long.'
  let parsed: URL
  try {
    parsed = new URL(u)
  } catch {
    return 'Paste a full link starting with https://.'
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return 'Only http:// and https:// links work.'
  return null
}

const clamp = (v: number, [lo, hi]: [number, number], fallback: number) =>
  Number.isFinite(v) ? Math.min(hi, Math.max(lo, v)) : fallback

/** The remembered model if the engine still offers it, else '' (its default);
 *  `fellBack` is true when a remembered choice had to be dropped. */
export function resolveModel(engine: Pick<TranslateEngine, 'models'> | undefined, saved: string): { model: string; fellBack: boolean } {
  if (!saved) return { model: '', fellBack: false }
  const ok = !!engine?.models?.includes(saved)
  return { model: ok ? saved : '', fellBack: !ok }
}

/** The POST body: numbers clamped as the service would, overlap at most half the chunk. */
export function buildStartBody(form: LiveForm): LiveSessionStart {
  const segment = Math.round(clamp(form.segment_seconds, SEGMENT_RANGE, DEFAULT_FORM.segment_seconds))
  const overlap = Math.min(clamp(form.overlap_seconds, OVERLAP_RANGE, DEFAULT_FORM.overlap_seconds), segment / 2)
  return {
    url: form.url.trim(),
    source_language: LIVE_LANGUAGES.some((l) => l.code === form.source_language) ? form.source_language : 'zh',
    whisper_size: WHISPER_SIZES.includes(form.whisper_size) ? form.whisper_size : 'small',
    segment_seconds: segment,
    overlap_seconds: overlap,
    engine: form.engine || null,
    model: form.model || null,
    max_minutes: clamp(form.max_minutes, MAX_MINUTES_RANGE, DEFAULT_FORM.max_minutes),
    use_gpu: form.use_gpu === true,
  }
}

export const isActive = (status: string | null | undefined) => status === 'queued' || status === 'running'

/** Append newly polled cues, keeping only the newest `cap`. */
export function appendCues(prev: LiveCue[], incoming: LiveCue[], cap = CUES_KEPT): LiveCue[] {
  if (!incoming.length) return prev
  const all = prev.concat(incoming)
  return all.length > cap ? all.slice(all.length - cap) : all
}

/** The newest `n` cues, newest first (the feed order). */
export const feedCues = (cues: LiveCue[], n = FEED_SHOWN) => cues.slice(-n).reverse()

/** "1:05" or "1:02:05" from seconds since the session started. */
export function fmtTs(sec: number): string {
  const s = Math.max(0, Math.floor(Number.isFinite(sec) ? sec : 0))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const ss = String(s % 60).padStart(2, '0')
  return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`
}

/** The session to show on load: an active one, else the most recent (list is oldest first). */
export function pickSession(list: LiveSessionSummary[]): string | null {
  const active = list.find((s) => isActive(s.status))
  return (active ?? list[list.length - 1])?.session_id ?? null
}

/** One status line for the session. */
export function statusLine(s: Pick<LiveSessionStatus, 'status' | 'message'> & { model?: string | null }, lines: number): string {
  const n = `${lines} line${lines === 1 ? '' : 's'}${s.model ? ` · ${s.model}` : ''}`
  switch (s.status) {
    case 'queued':
      return s.message || 'Waiting for the GPU…'
    case 'running':
      return `${s.message || 'Capturing…'} · ${n}`
    case 'done':
      return `Finished · ${n}`
    case 'cancelled':
      return `Stopped · ${n}`
    default:
      return `Stopped with an error: ${s.message || 'the live session failed.'}`
  }
}

/** One line for the Advanced summary (its current values). */
export function advancedSummary(f: LiveForm): string {
  return `Whisper ${f.whisper_size} · chunk ${f.segment_seconds}s · overlap ${f.overlap_seconds}s · stop after ${f.max_minutes} min · ${f.use_gpu ? 'GPU' : 'CPU'}`
}

// A 403 on start: from another device this needs a permission the owner grants.
export const LIVE_FORBIDDEN =
  "This account can't start live capture. It needs the “import from a link” permission, and a paid engine also needs “paid engines”. Ask the owner of the main PC, or pick a free engine."

/** Plain text for a failed start or stop. */
export function describeLiveError(err: unknown): string {
  const e = err as Partial<ApiError> | null
  if (e?.status === 403) return LIVE_FORBIDDEN
  if (e?.status === 409) return 'A live session is already running. Stop it first.'
  const { title, detail } = describeError(err, { serverText: true })
  return detail ? `${title} ${detail}` : title
}
