import type { DramaDetail } from '../../api/types'
import type { AutofillRequest, MediaAnalysis } from '../../types/workspace'

// Pure logic for the Source stage's Auto-fill and Analyze-media panels.

const LABELS: Record<string, string> = {
  title_en: 'English title',
  title_zh: 'Chinese title',
  author: 'Author',
  studio: 'Studio',
  director: 'Director',
  voice_actors: 'Voice actors',
  summary: 'Summary',
  source_url: 'Source URL',
}

export interface SuggestionRow {
  key: string
  label: string
  suggested: string
  current: string
  // The drama already has a different value here; never pre-selected.
  conflict: boolean
  // Same as the current value: nothing to apply.
  same: boolean
}

export function suggestionRows(suggestion: Record<string, string>, drama: DramaDetail): SuggestionRow[] {
  const cur = drama as unknown as Record<string, unknown>
  return Object.keys(LABELS)
    .filter((k) => typeof suggestion[k] === 'string' && suggestion[k].trim() !== '')
    .map((key) => {
      const current = typeof cur[key] === 'string' ? (cur[key] as string).trim() : ''
      const suggested = suggestion[key].trim()
      const same = current === suggested
      return { key, label: LABELS[key], suggested, current, same, conflict: current !== '' && !same }
    })
}

// Only fields that are empty on the drama start ticked, so accepting
// without looking cannot overwrite what the user typed.
export function defaultSelection(rows: SuggestionRow[]): Set<string> {
  return new Set(rows.filter((r) => !r.conflict && !r.same).map((r) => r.key))
}

export function acceptedFields(rows: SuggestionRow[], selected: Set<string>): Record<string, string> {
  const out: Record<string, string> = {}
  for (const r of rows) if (selected.has(r.key) && !r.same) out[r.key] = r.suggested
  return out
}

// Exactly one of url / page_text, as the API requires; null when neither is usable.
export function autofillRequest(url: string, text: string): AutofillRequest | null {
  const u = url.trim()
  const t = text.trim()
  if (u && t) return null
  if (u) return /^https?:\/\//i.test(u) ? { url: u } : null
  return t ? { page_text: t } : null
}

export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const ss = String(s % 60).padStart(2, '0')
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`
}

export function analysisSummary(a: MediaAnalysis): string {
  const kind = a.has_video ? (a.has_audio ? 'video + audio' : 'video only') : a.has_audio ? 'audio only' : 'no streams'
  return `${formatDuration(a.duration_seconds)} · ${kind}`
}

export function analysisDetails(a: MediaAnalysis): [string, string][] {
  const tracks = a.subtitle_tracks ?? []
  return [
    ['Duration', formatDuration(a.duration_seconds)],
    ['Resolution', !a.has_video ? 'audio only' : a.width && a.height ? `${a.width}×${a.height}` : 'unknown'],
    ['Frame rate', a.fps ? `${a.fps.toFixed(2)} fps` : '—'],
    ['Audio', a.has_audio ? 'yes' : 'no'],
    ['Audio tracks', String(a.audio_track_count)],
    ['Sample rate', a.sample_rate ? `${a.sample_rate} Hz` : 'unknown'],
    ['Subtitle tracks', tracks.length ? subtitleTrackList(a) : 'none'],
  ]
}

// "2 (Chinese ASS, unknown language SubRip)" style list of embedded subtitles.
export function subtitleTrackList(a: MediaAnalysis): string {
  const tracks = a.subtitle_tracks ?? []
  const one = (t: { codec: string; language: string | null }) =>
    `${t.language ? languageName(t.language) : 'unknown language'} ${codecName(t.codec)}`
  return `${tracks.length} (${tracks.map(one).join(', ')})`
}

const CODECS: Record<string, string> = {
  ass: 'ASS', ssa: 'SSA', subrip: 'SubRip', srt: 'SubRip', mov_text: 'MP4 text', webvtt: 'WebVTT',
  hdmv_pgs_subtitle: 'PGS (image)', dvd_subtitle: 'DVD (image)', dvb_subtitle: 'DVB (image)',
}
const codecName = (c: string) => CODECS[c.toLowerCase()] ?? c

// ffprobe tags are ISO 639-2 (chi/zho, jpn, kor, eng); unknown codes stay as they are.
const LANGS: Record<string, string> = {
  chi: 'Chinese', zho: 'Chinese', zh: 'Chinese', jpn: 'Japanese', ja: 'Japanese', kor: 'Korean', ko: 'Korean',
  eng: 'English', en: 'English',
}
const languageName = (l: string) => LANGS[l.toLowerCase()] ?? l

// The media type to offer from "Use this content type", or null when there is
// no usable guess or the drama already has it.
export function contentTypeSuggestion(a: MediaAnalysis, current: string | null, allowed: string[]): string | null {
  const g = a.content_type_guess
  if (!g || !allowed.includes(g) || g === current) return null
  return g
}
