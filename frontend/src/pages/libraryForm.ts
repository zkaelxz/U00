import type { DramaCreateRequest } from '../types/library'

// Caps mirror api/schemas.py DramaCreateRequest.
export const MAX_NAME_LEN = 300
export const MAX_SUMMARY_LEN = 5000
export const SOURCE_LANGUAGES = ['zh', 'ja', 'ko']
export const MEDIA_TYPES = [
  'audio_drama', 'video_drama', 'anime', 'novel', 'manhwa', 'manga', 'manhua',
  'asmr', 'streamer_vod', 'music', 'other',
]

export function validateCreate(form: DramaCreateRequest): string | null {
  if (!SOURCE_LANGUAGES.includes(form.source_language)) return 'Choose a source language.'
  if (!(form.title_en ?? '').trim() && !(form.title_zh ?? '').trim()) {
    return 'Enter an English or original title.'
  }
  for (const key of ['title_en', 'title_zh', 'author', 'studio'] as const) {
    if ((form[key] ?? '').length > MAX_NAME_LEN) {
      return `${key} is too long (max ${MAX_NAME_LEN} characters).`
    }
  }
  if ((form.summary ?? '').length > MAX_SUMMARY_LEN) {
    return `Summary is too long (max ${MAX_SUMMARY_LEN} characters).`
  }
  return null
}

export const canConfirmDelete = (typed: string) => typed === 'DELETE'

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
