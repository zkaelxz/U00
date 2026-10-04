// Discover page shapes (api/schemas/sources.py KnownTitle*, Discover*).

export interface KnownTitle {
  id: number
  title_original: string | null
  title_en: string | null
  author: string | null
  tags: string | null
  summary_en: string | null
  summary_original: string | null
  source_name: string | null
  source_url: string | null
  language: string | null
  media_type: string | null
  created_at: string | null
}

export interface KnownTitleList {
  titles: KnownTitle[]
  total: number
}

export interface KnownTitleCreate {
  title_original: string
  title_en: string
  author: string
  tags: string
  summary_en: string
  source_name: string
  source_url: string
  language: string
  media_type: string
}

export interface TitleFilters {
  q?: string
  language?: string
  media_type?: string
}

export interface Platform {
  name: string
  url: string
  region?: string
  language?: string
  content_types?: string[]
  notes?: string
}

export type SearchGenre = 'baihe' | 'any'

export interface SearchLink {
  site: string
  url: string
  note?: string
  kind?: string
}

export interface TranslateQueryResult {
  query: string
  translated: string
  engine: string
}

export interface BaihehubHit {
  title: string
  url: string
  snippet: string
}

export interface BaihehubResult {
  results: BaihehubHit[]
  fallback_url: string
}

export interface ImportSuggestion {
  suggestion: Record<string, string>
  found: boolean
  needs_manual: boolean
  message: string
}

export interface DiscoverJobStarted {
  job_id: string
  started: boolean
}

export interface BulkEntry {
  title: string
  author: string
  tags: string
  source_url: string
  has_audio_drama: boolean
  entry_id?: string | null
  language?: string
}

export interface BulkPage {
  url: string
  ok: boolean
  needs_manual: boolean
  count: number
  message: string
}

export interface BulkExtractResult {
  entries: BulkEntry[]
  pages: BulkPage[]
  source_label: string
}

export interface BulkCommitResult {
  added: number
  skipped: number
  ids: number[]
}

export interface NavigationHelpResult {
  labels: Record<string, string>
  steps: string
  needs_manual: boolean
  message: string
}

export interface ImportedDrama {
  id: number
}
