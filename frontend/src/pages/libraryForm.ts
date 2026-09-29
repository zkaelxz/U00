import type { DramaCreateRequest, DramaDeleteResult } from '../types/library'

// Caps mirror api/schemas.py DramaCreateRequest.
export const MAX_NAME_LEN = 300
export const MAX_SUMMARY_LEN = 5000
export const SOURCE_LANGUAGES = ['zh', 'ja', 'ko']
// Choices offered when creating a drama. 'music' and 'other' were dropped from
// the picker (user decision 2026-09-29); the API still accepts them so older
// dramas that use them keep loading and saving.
export const MEDIA_TYPES = [
  'audio_drama', 'video_drama', 'anime', 'novel', 'manhwa', 'manga', 'manhua',
  'asmr', 'streamer_vod',
]

export function validateCreate(form: DramaCreateRequest): string | null {
  if (!SOURCE_LANGUAGES.includes(form.source_language)) return 'Choose a source language.'
  if (!(form.title_en ?? '').trim() && !(form.title_zh ?? '').trim()) {
    return 'Enter an English or original title.'
  }
  for (const key of ['title_en', 'title_zh', 'author', 'studio', 'director', 'voice_actors'] as const) {
    if ((form[key] ?? '').length > MAX_NAME_LEN) {
      return `${key} is too long (max ${MAX_NAME_LEN} characters).`
    }
  }
  if ((form.summary ?? '').length > MAX_SUMMARY_LEN) {
    return `Summary is too long (max ${MAX_SUMMARY_LEN} characters).`
  }
  if (form.new_series_name !== undefined) {
    if (!form.new_series_name.trim()) return 'Enter a name for the new series.'
    if (form.new_series_name.length > MAX_NAME_LEN) return `Series name is too long (max ${MAX_NAME_LEN} characters).`
  }
  return null
}

// Series picker value for "create a new series".
export const NEW_SERIES = 'new'

export interface CreateExtras {
  series: string // '' none, NEW_SERIES, or a series id
  newSeriesName: string
  preset: string // '' none, or a preset id
}

// The body POST /api/dramas gets: blank optional text is left out, and
// series_id / new_series_name are never both sent.
export function buildCreateRequest(form: DramaCreateRequest, extras: CreateExtras): DramaCreateRequest {
  const out: DramaCreateRequest = { source_language: form.source_language }
  for (const k of ['title_en', 'title_zh', 'author', 'studio', 'director', 'voice_actors', 'summary'] as const) {
    const v = (form[k] ?? '').trim()
    if (v) out[k] = v
  }
  if (form.media_type) out.media_type = form.media_type
  if (extras.series === NEW_SERIES) out.new_series_name = extras.newSeriesName
  else if (extras.series) out.series_id = Number(extras.series)
  if (extras.preset) out.preset_id = Number(extras.preset)
  return out
}

export const canConfirmDelete = (typed: string) => typed === 'DELETE'

// The note to keep on screen after a delete: the server's own plain-English
// warning when the drama is gone but some of its files were left behind.
export const deleteNotice = (r: DramaDeleteResult): string | null => r.warning?.trim() || null

// A Library "More" section with nothing in it renders nothing (react-ui-guidelines rule 8);
// an error keeps it visible so a failed load is not silently hidden.
export const showFold = (count: number | undefined, error: unknown): boolean => !(count === 0 && !error)

export interface HistoryGroup<T> {
  entry: T // the most recent row of the run
  count: number
}

// Collapses runs of consecutive rows for the same drama (the list is newest
// first, and the API records one row per progress save) into one row + count.
export function groupHistory<T extends { drama_id: number }>(rows: T[]): HistoryGroup<T>[] {
  const out: HistoryGroup<T>[] = []
  for (const row of rows) {
    const last = out[out.length - 1]
    if (last && last.entry.drama_id === row.drama_id) last.count += 1
    else out.push({ entry: row, count: 1 })
  }
  return out
}
