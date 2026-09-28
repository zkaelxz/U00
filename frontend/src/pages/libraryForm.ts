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
