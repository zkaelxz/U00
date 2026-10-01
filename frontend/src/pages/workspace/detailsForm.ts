import type { DramaDetail } from '../../api/types'
import type { DramaMetadataUpdate } from '../../types/library'
import { humanize } from '../../components/labels'
import { MAX_NAME_LEN, MAX_SUMMARY_LEN, MEDIA_TYPES, SOURCE_LANGUAGES } from '../libraryForm'

// Pure logic for the Source stage's "Edit details" form. Caps mirror
// api/schemas.py DramaMetadataUpdate; the server re-validates everything.

export const MAX_TAGS_LEN = 2000
export const MAX_URL_LEN = 2000
export const MAX_COUNT = 2147483647
// services/drama_service.py PUBLICATION_STATUSES.
export const PUBLICATION_STATUSES = ['unknown', 'ongoing', 'completed', 'hiatus']

export interface DetailsForm {
  title_en: string
  title_zh: string
  author: string
  studio: string
  director: string
  voice_actors: string
  summary: string
  genre: string
  source_url: string
  chapter_count: string // '' = not set
  episode_number: string // '' = not set
  episode_summary: string
  publication_status: string // '' = not set
  custom_tags: string // comma-separated, as stored
  media_type: string
  source_language: string
  series_id: string // '' = no series, NEW_SERIES = "+ New series…"
  new_series_name: string // used when series_id is NEW_SERIES
}

export const NEW_SERIES = 'new'

export type DetailsErrors = Partial<Record<keyof DetailsForm, string>>

const TEXT_KEYS = [
  'title_en', 'title_zh', 'author', 'studio', 'director', 'voice_actors', 'summary', 'genre', 'source_url',
  'episode_summary',
] as const
const COUNT_KEYS = ['chapter_count', 'episode_number'] as const
const LONG_KEYS: readonly string[] = ['summary', 'episode_summary']

export const FIELD_LABELS: Record<keyof DetailsForm, string> = {
  title_en: 'English title',
  title_zh: 'Original title',
  author: 'Author',
  studio: 'Studio',
  director: 'Director',
  voice_actors: 'Voice actors',
  summary: 'Summary',
  genre: 'Genre',
  source_url: 'Source URL',
  chapter_count: 'Chapter count',
  episode_number: 'Episode number',
  episode_summary: 'Running episode summary',
  publication_status: 'Publication status',
  custom_tags: 'Tags',
  media_type: 'Media type',
  source_language: 'Source language',
  series_id: 'Series',
  new_series_name: 'New series name',
}

export const normalizeTags = (raw: string): string =>
  raw
    .split(',')
    .map((t) => t.trim())
    .filter(Boolean)
    .join(', ')

export function formFromDrama(d: DramaDetail): DetailsForm {
  return {
    title_en: d.title_en ?? '',
    title_zh: d.title_zh ?? '',
    author: d.author ?? '',
    studio: d.studio ?? '',
    director: d.director ?? '',
    voice_actors: d.voice_actors ?? '',
    summary: d.summary ?? '',
    genre: d.genre ?? '',
    source_url: d.source_url ?? '',
    chapter_count: d.chapter_count ? String(d.chapter_count) : '',
    episode_number: d.episode_number ? String(d.episode_number) : '',
    episode_summary: d.episode_summary ?? '',
    publication_status: d.publication_status ?? '',
    custom_tags: (d.custom_tags ?? []).join(', '),
    media_type: d.media_type ?? 'audio_drama',
    source_language: d.source_language ?? 'zh',
    series_id: d.series_id == null ? '' : String(d.series_id),
    new_series_name: '',
  }
}

// The picker offers MEDIA_TYPES only ('music'/'other' were dropped), but an
// existing value outside that list stays selectable so it is shown and kept.
export const mediaTypeOptions = (current: string): string[] =>
  current && !MEDIA_TYPES.includes(current) ? [current, ...MEDIA_TYPES] : MEDIA_TYPES

export function validateDetails(f: DetailsForm, initial: DetailsForm): DetailsErrors {
  const e: DetailsErrors = {}
  if (!f.title_en.trim() && !f.title_zh.trim()) e.title_en = 'Enter an English or original title.'
  for (const k of TEXT_KEYS) {
    const cap = LONG_KEYS.includes(k) ? MAX_SUMMARY_LEN : k === 'source_url' ? MAX_URL_LEN : MAX_NAME_LEN
    if (f[k].length > cap) e[k] = `Too long (max ${cap} characters).`
  }
  const url = f.source_url.trim()
  if (f.source_url !== initial.source_url && url && !e.source_url && !/^https?:\/\//i.test(url)) e.source_url = 'Start the link with http:// or https://.'
  for (const k of COUNT_KEYS) {
    const v = f[k].trim()
    if (v && (!/^\d+$/.test(v) || Number(v) > MAX_COUNT)) e[k] = 'Enter a whole number, or leave it empty.'
  }
  if (f.publication_status !== initial.publication_status && !PUBLICATION_STATUSES.includes(f.publication_status)) {
    e.publication_status = 'Choose a publication status.'
  }
  if (normalizeTags(f.custom_tags).length > MAX_TAGS_LEN) e.custom_tags = `Too long (max ${MAX_TAGS_LEN} characters).`
  if (!SOURCE_LANGUAGES.includes(f.source_language)) e.source_language = 'Choose a source language.'
  if (f.media_type !== initial.media_type && !MEDIA_TYPES.includes(f.media_type)) e.media_type = 'Choose a media type.'
  const seriesProblem = newSeriesProblem(f.series_id, f.new_series_name)
  if (seriesProblem) e.new_series_name = seriesProblem
  return e
}

export interface DetailsPayload {
  metadata: DramaMetadataUpdate // for POST /api/dramas/{id}/metadata
  sourceLanguage: string | null // for POST /api/source/dramas/{id}/config
}

// Only fields that differ from what was loaded are sent.
export function buildDetailsPayload(f: DetailsForm, initial: DetailsForm): DetailsPayload {
  const metadata: DramaMetadataUpdate = {}
  for (const k of TEXT_KEYS) {
    const v = k === 'source_url' ? f[k].trim() : f[k]
    if (v !== initial[k]) metadata[k] = v
  }
  // Counts: '' (or 0) clears, sent as 0.
  for (const k of COUNT_KEYS) {
    const v = Number(f[k].trim() || 0)
    if (v !== Number(initial[k] || 0)) metadata[k] = v
  }
  if (f.publication_status !== initial.publication_status && f.publication_status) {
    metadata.publication_status = f.publication_status
  }
  const tags = normalizeTags(f.custom_tags)
  if (tags !== normalizeTags(initial.custom_tags)) metadata.custom_tags = tags
  if (f.media_type !== initial.media_type) metadata.media_type = f.media_type
  if (f.series_id !== initial.series_id) Object.assign(metadata, seriesUpdate(f.series_id, f.new_series_name))
  return {
    metadata,
    sourceLanguage: f.source_language !== initial.source_language ? f.source_language : null,
  }
}

// Parity P11/X09: the metadata update for a series choice. series_id 0
// takes the drama out of its series; "+ New series…" sends the name, which
// the server creates (or reuses) for the caller.
export function seriesUpdate(choice: string, newName: string): DramaMetadataUpdate {
  if (choice === NEW_SERIES) return { new_series_name: newName.trim() }
  return { series_id: choice === '' ? 0 : Number(choice) }
}

export function newSeriesProblem(choice: string, newName: string): string | null {
  if (choice !== NEW_SERIES) return null
  const name = newName.trim()
  if (!name) return 'Enter a name for the new series.'
  if (name.length > MAX_NAME_LEN) return `Too long (max ${MAX_NAME_LEN} characters).`
  return null
}

// The form after the drama changed underneath it: fields the user edited
// (form differs from what was loaded) stay; everything else takes the new value.
export function reseedForm(form: DetailsForm, initial: DetailsForm, next: DetailsForm): DetailsForm {
  const out = { ...next }
  for (const k of Object.keys(next) as (keyof DetailsForm)[]) if (form[k] !== initial[k]) out[k] = form[k]
  // A half-typed new series name only matters while "+ New series…" is picked.
  if (out.series_id !== NEW_SERIES) out.new_series_name = ''
  return out
}

export const isEmptyPayload = (p: DetailsPayload) => !Object.keys(p.metadata).length && p.sourceLanguage === null

// Maps an API 422 onto a form field: request validation details carry
// `loc` (["body", "author"]); service errors name the field in the message.
export function serverFieldErrors(details: unknown, message: string): DetailsErrors {
  const keys = Object.keys(FIELD_LABELS) as (keyof DetailsForm)[]
  const out: DetailsErrors = {}
  if (Array.isArray(details)) {
    for (const d of details as { loc?: unknown[]; msg?: string }[]) {
      const k = d?.loc?.[d.loc.length - 1]
      if (typeof k === 'string' && keys.includes(k as keyof DetailsForm)) out[k as keyof DetailsForm] = d.msg || 'Invalid value.'
    }
  }
  if (!Object.keys(out).length) {
    // Longest name first: "episode_summary" must not match "summary".
    const k = [...keys].sort((x, y) => y.length - x.length).find((key) => message.includes(key))
    if (k) out[k] = message
  }
  return out
}

// Source-stage content/transcript mode (services/source_service.py).
export const CONTENT_MODES = ['audio_drama', 'streamer_vod', 'novel_narration']

const TRANSCRIPT_MODE_LABELS: Record<string, string> = {
  have_transcript: 'I have a transcript',
  whisper: 'Whisper (speech to text)',
  hardsub_ocr: 'Hardsub OCR (read burned-in subtitles)',
}

// Media type, content mode or transcript mode -> its display label.
export const modeLabel = (m: string) => TRANSCRIPT_MODE_LABELS[m] ?? humanize('mediaType', m)
