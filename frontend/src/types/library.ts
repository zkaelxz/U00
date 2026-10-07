import type { DramaDetail } from '../api/types'

// Mirrors the Library* / DramaCreate* / DramaDelete* models in api/schemas/library.py.

export interface LibraryUsage {
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  estimated_cost_usd: number
  call_count: number
}

export interface LibraryDashboard {
  total_dramas: number
  by_status: Record<string, number>
  by_media_type: Record<string, number>
  total_lines: number
  translated_lines: number
  usage: LibraryUsage
}

export interface LibraryDramaRef {
  id: number
  title_en: string | null
  title_zh: string | null
  status: string | null
  updated_at: string | null
  media_type: string | null
}

export interface LibraryCostRow {
  id: number
  title_en: string | null
  title_zh: string | null
  translation_engine: string | null
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  estimated_cost_usd: number
  call_count: number
}

export interface LibrarySeries {
  id: number
  name: string
  is_private?: boolean
  owned_by_me?: boolean
  character_count: number
  glossary_term_count: number
  dramas: LibraryDramaRef[]
}

export interface LibrarySearchHit {
  drama_id: number
  idx: number
  zh: string | null
  en: string | null
  title_en: string | null
  title_zh: string | null
}

export interface LibrarySearchResponse {
  count: number
  items: LibrarySearchHit[]
}

export interface LibraryHistoryEntry {
  drama_id: number
  line_idx: number | null
  percent_complete: number | null
  accessed_at: string | null
  title_en: string | null
  title_zh: string | null
}

// GET /api/library/continue: partly-read dramas, most recent first.
export interface LibraryContinueEntry {
  drama_id: number
  title_en: string | null
  title_zh: string | null
  percent_complete: number | null
  last_page: number | null
  last_accessed_at: string | null
  has_cover_art: boolean
}

// GET /api/library/filter-options: the "All dramas" filters' data-driven choices.
export interface LibraryFilterOptions {
  studios: string[]
  authors: string[]
  voice_actors: string[]
  custom_tags: string[]
}

export interface ReadingHistoryClearResult {
  cleared: boolean
  removed: number
}

export interface LibraryPreset {
  id: number
  name: string
  translation_engine: string | null
  engine_model: string | null
  style_preset: string | null
  locale: string | null
}

export interface LibraryVoice {
  id: number
  name: string
  language: string | null
  clone_engine: string | null
  source_drama: string | null
  clip_available: boolean
}

export interface DramaCreateRequest {
  source_language: string
  title_en?: string
  title_zh?: string
  author?: string
  studio?: string
  director?: string
  voice_actors?: string
  summary?: string
  media_type?: string
  // series_id and new_series_name are mutually exclusive.
  series_id?: number
  new_series_name?: string
  preset_id?: number
}

// api/schemas/library.py DramaMetadataUpdate: partial, only sent keys are written.
export interface DramaMetadataUpdate {
  title_en?: string
  title_zh?: string
  author?: string
  studio?: string
  director?: string
  voice_actors?: string
  summary?: string
  genre?: string
  custom_tags?: string
  source_url?: string // '' clears; else must start with http:// or https://
  episode_summary?: string
  chapter_count?: number // 0 clears
  episode_number?: number // 0 clears
  default_female_pronouns?: boolean // the Translate stage's she/her default
  include_genre_notes?: boolean // the Translate stage's genre guidance
  media_type?: string
  publication_status?: string // unknown / ongoing / completed / hiatus
  series_id?: number // 0 takes the drama out of its series
  new_series_name?: string // not with series_id
}

export interface DramaDeleteResult {
  deleted: boolean
  drama_id: number
  // Set when the drama is gone but some of its files could not be removed.
  warning?: string | null
}

// api/schemas/library.py DramaPresetDefaults: a preset's session-only values, which
// the client holds (only the preset's engine is saved on the drama).
export interface DramaPresetDefaults {
  style_preset: string | null
  locale: string | null
  default_female_pronouns: boolean
  include_genre_notes: boolean
  engine_model?: string | null // for the drama's saved translation_engine
}

// api/schemas/library.py DramaCreateResult: the new drama plus its preset's values.
export interface DramaCreateResult extends DramaDetail {
  preset_defaults?: DramaPresetDefaults | null
}

interface Items<T> {
  items: T[]
}
export type LibraryRecentResponse = Items<LibraryDramaRef>
export type LibraryCostResponse = Items<LibraryCostRow>
export type LibrarySeriesResponse = Items<LibrarySeries>
export type LibraryHistoryResponse = Items<LibraryHistoryEntry>
export type LibraryContinueResponse = Items<LibraryContinueEntry>
export type LibraryPresetsResponse = Items<LibraryPreset>
export type LibraryVoiceBankResponse = Items<LibraryVoice>
