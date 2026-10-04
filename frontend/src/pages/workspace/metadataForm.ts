import type { DramaDetail } from '../../api/types'
import type { AutofillRequest, MediaAnalysis } from '../../types/workspace'
import { humanize } from '../../components/labels'

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
    ['Resolution', !a.has_video ? 'Audio only' : a.width && a.height ? `${a.width}×${a.height}` : 'Unknown'],
    ['Frame rate', a.fps ? `${a.fps.toFixed(2)} fps` : '—'],
    ['Audio', a.has_audio ? 'Yes' : 'No'],
    ['Audio tracks', String(a.audio_track_count)],
    ['Sample rate', a.sample_rate ? `${a.sample_rate} Hz` : 'Unknown'],
    ['Subtitle tracks', tracks.length ? subtitleTrackList(a) : 'None'],
  ]
}

// "2 (Chinese ASS, unknown language SubRip)" style list of embedded subtitles.
function subtitleTrackList(a: MediaAnalysis): string {
  const tracks = a.subtitle_tracks ?? []
  return `${tracks.length} (${tracks.map(trackName).join(', ')})`
}

const trackName = (t: { codec: string; language: string | null }) =>
  `${t.language ? languageName(t.language) : 'unknown language'} ${codecName(t.codec)}`

const CODECS: Record<string, string> = {
  ass: 'ASS', ssa: 'SSA', subrip: 'SubRip', srt: 'SubRip', mov_text: 'MP4 text', webvtt: 'WebVTT',
  hdmv_pgs_subtitle: 'PGS (image)', dvd_subtitle: 'DVD (image)', dvb_subtitle: 'DVB (image)',
}
const codecName = (c: string) => CODECS[c.toLowerCase()] ?? c

// ffprobe tags are ISO 639-2 (chi/zho, jpn, kor, eng); mapped to the app's
// codes for humanize, with a few common others named here.
const ISO3: Record<string, string> = { chi: 'zh', zho: 'zh', jpn: 'ja', kor: 'ko', eng: 'en' }
const OTHER_LANGS: Record<string, string> = {
  fre: 'French', fra: 'French', spa: 'Spanish', ger: 'German', deu: 'German', rus: 'Russian',
  por: 'Portuguese', ita: 'Italian', tha: 'Thai', vie: 'Vietnamese', ind: 'Indonesian', ara: 'Arabic',
}
const languageName = (l: string) => {
  const code = l.toLowerCase()
  return OTHER_LANGS[code] ?? humanize('language', ISO3[code] ?? code)
}

// The suggested steps in words. The server's "Import existing subtitle
// track (chi, unknown) …" carries raw codes; it is rebuilt from the tracks.
export function pipelineSteps(a: MediaAnalysis): string[] {
  const tracks = a.subtitle_tracks ?? []
  return (a.suggested_pipeline ?? []).map((step) =>
    step.startsWith('Import existing subtitle track') && tracks.length
      ? `Import the existing subtitle track${tracks.length === 1 ? '' : 's'} (${tracks.map(trackName).join(', ')}) instead of transcribing`
      : step,
  )
}

// The media type to offer from "Use this content type", or null when there is
// no usable guess or the drama already has it.
export function contentTypeSuggestion(a: MediaAnalysis, current: string | null, allowed: string[]): string | null {
  const g = a.content_type_guess
  if (!g || !allowed.includes(g) || g === current) return null
  return g
}
