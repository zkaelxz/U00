// Mirrors the Library* / DramaCreate* / DramaDelete* models in api/schemas.py.

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
  summary?: string
  media_type?: string
}

export interface DramaDeleteResult {
  deleted: boolean
  drama_id: number
}

interface Items<T> {
  items: T[]
}
export type LibraryRecentResponse = Items<LibraryDramaRef>
export type LibraryCostResponse = Items<LibraryCostRow>
export type LibrarySeriesResponse = Items<LibrarySeries>
export type LibraryHistoryResponse = Items<LibraryHistoryEntry>
export type LibraryPresetsResponse = Items<LibraryPreset>
export type LibraryVoiceBankResponse = Items<LibraryVoice>
