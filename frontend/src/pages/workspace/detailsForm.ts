import type { DramaDetail } from '../../api/types'
import type { DramaMetadataUpdate } from '../../types/library'
import type { SourceConfig, SourceConfigUpdate } from '../../types/workspace'
import { humanize } from '../../components/labels'
import { MAX_NAME_LEN, MAX_SUMMARY_LEN, MEDIA_TYPES, SOURCE_LANGUAGES } from '../libraryForm'

// Pure logic for the Source stage's "Edit details" form. Caps mirror
// api/schemas.py DramaMetadataUpdate; the server re-validates everything.

export const MAX_TAGS_LEN = 2000

export interface DetailsForm {
  title_en: string
  title_zh: string
  author: string
  studio: string
  director: string
  voice_actors: string
  summary: string
  custom_tags: string // comma-separated, as stored
  media_type: string
  source_language: string
  series_id: string // '' = no series
}

export type DetailsErrors = Partial<Record<keyof DetailsForm, string>>

const TEXT_KEYS = ['title_en', 'title_zh', 'author', 'studio', 'director', 'voice_actors', 'summary'] as const

export const FIELD_LABELS: Record<keyof DetailsForm, string> = {
  title_en: 'English title',
  title_zh: 'Original title',
  author: 'Author',
  studio: 'Studio',
  director: 'Director',
  voice_actors: 'Voice actors',
  summary: 'Summary',
  custom_tags: 'Tags',
  media_type: 'Media type',
  source_language: 'Source language',
  series_id: 'Series',
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
    custom_tags: (d.custom_tags ?? []).join(', '),
    media_type: d.media_type ?? 'audio_drama',
    source_language: d.source_language ?? 'zh',
    series_id: d.series_id == null ? '' : String(d.series_id),
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
    const cap = k === 'summary' ? MAX_SUMMARY_LEN : MAX_NAME_LEN
    if (f[k].length > cap) e[k] = `Too long (max ${cap} characters).`
  }
  if (normalizeTags(f.custom_tags).length > MAX_TAGS_LEN) e.custom_tags = `Too long (max ${MAX_TAGS_LEN} characters).`
  if (!SOURCE_LANGUAGES.includes(f.source_language)) e.source_language = 'Choose a source language.'
  if (f.media_type !== initial.media_type && !MEDIA_TYPES.includes(f.media_type)) e.media_type = 'Choose a media type.'
  if (f.series_id === '' && initial.series_id !== '') e.series_id = 'Removing a series is not supported yet.'
  return e
}

export interface DetailsPayload {
  metadata: DramaMetadataUpdate // for POST /api/dramas/{id}/metadata
  sourceLanguage: string | null // for POST /api/source/dramas/{id}/config
}

// Only fields that differ from what was loaded are sent.
export function buildDetailsPayload(f: DetailsForm, initial: DetailsForm): DetailsPayload {
  const metadata: DramaMetadataUpdate = {}
  for (const k of TEXT_KEYS) if (f[k] !== initial[k]) metadata[k] = f[k]
  const tags = normalizeTags(f.custom_tags)
  if (tags !== normalizeTags(initial.custom_tags)) metadata.custom_tags = tags
  if (f.media_type !== initial.media_type) metadata.media_type = f.media_type
  if (f.series_id !== initial.series_id && f.series_id !== '') metadata.series_id = Number(f.series_id)
  return {
    metadata,
    sourceLanguage: f.source_language !== initial.source_language ? f.source_language : null,
  }
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
    const k = keys.find((key) => message.includes(key))
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

// Only the modes that differ from the loaded config.
export function modeUpdate(config: SourceConfig, content: string, transcript: string): SourceConfigUpdate {
  const out: SourceConfigUpdate = {}
  if (content && content !== config.content_mode) out.content_mode = content
  if (transcript && transcript !== config.transcript_mode) out.transcript_mode = transcript
  return out
}
