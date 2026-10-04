// Hand-maintained mirrors of the API contract (api/schemas/ and the
// api/*_schemas.py modules). There is no codegen: change both sides together.

export interface ErrorInfo {
  code:
    | 'validation_error'
    | 'not_found'
    | 'unsupported_operation'
    | 'dependency_unavailable'
    | 'application_error'
    | 'internal_error'
    | string
  message: string
  details?: unknown
}

export interface HealthResponse {
  status: string
}

export interface MetaResponse {
  app: string
  api_version: string
  environment: string
  // True when the viewer is at the PC (PC-only routes would allow the request).
  local?: boolean
}

export interface DramaSummary {
  id: number
  title_zh: string | null
  title_en: string | null
  author: string | null
  studio: string | null
  director: string | null
  voice_actors: string | null
  status: string | null
  source_language: string | null
  media_type: string | null
  content_mode: string | null
  series_id: number | null
  translation_engine: string | null
  custom_tags: string[]
  created_at: string | null
  updated_at: string | null
  // Library list only: hidden from the household, and created by the signed-in viewer.
  is_private?: boolean | null
  owned_by_me?: boolean | null
}

export interface DramaDetail extends DramaSummary {
  summary: string | null
  genre: string | null
  publication_status: string | null
  chapter_count: number | null
  source_url?: string | null
  episode_number?: number | null
  episode_summary?: string | null
  narration_language: string | null
  author_romanized: string | null
  studio_romanized: string | null
  director_romanized: string | null
  voice_actors_romanized: string | null
  series_instructions: string | null
  has_audio: boolean
  has_novel_reference: boolean
  has_cover_art: boolean
}

export interface DramaListResponse {
  items: DramaSummary[]
  count: number
}

export interface DramaFilters {
  search?: string
  studio?: string
  author?: string
  voice_actor?: string
  status?: string
  source_language?: string
  media_type?: string
  quick_filter?: string
  tag?: string[]
}
