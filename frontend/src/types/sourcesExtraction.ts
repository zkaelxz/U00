// Mirrors api/sources_extraction_schemas.py and the job results of
// services/sources_import_service.py (Sources parity SO09, SO06, SO10): the
// pasted-URL AI fallback, the comic import and Review extraction. Names only; no key ever
// reaches the browser. URLs are scheme+host+path.

// GET /api/sources/url/ai-engines
export interface AiEngines {
  engines: string[]
  // The saved default engine, when the fallback can use it.
  default: string | null
}

// The opt-in fields a URL import sends (omitted = off).
export interface AiRequestFields {
  use_ai?: boolean
  engine?: string
  // Open a Review extraction instead of writing (SO10).
  review?: boolean
}

// One image the comic import left out, and why.
export interface SkippedImage {
  display_url: string | null
  reason: string
}

// POST /api/sources/url/import-comic result (job sourceimport_<drama_id>).
// needs_review: nothing was written.
export interface ComicUrlImportResult {
  kind: 'comic_import'
  needs_review: boolean
  pages_added: number
  skipped: SkippedImage[]
  skipped_count: number
  // A Review extraction was opened for the drama (GET .../extraction).
  review_open?: boolean
}

// ---------------------------------------------------------------- SO10 review

export type ReviewWhy = 'low_confidence' | 'asked' | 'diagnostics'
export type ConfidenceBucket = 'HIGH' | 'MEDIUM' | 'LOW' | 'FAILED'

export interface FieldConfidence {
  field: string
  bucket: ConfidenceBucket | string | null
  score: number
  checks: string[]
  value: string | null
}

export interface ExtractionContainer {
  selector: string
  chars: number
  preview: string
  exclusions: { selector: string; preview: string }[]
}

export interface ExtractionNovel {
  text_preview: string
  char_count: number
  chapter_title: string
  containers: ExtractionContainer[]
  content_selector: string | null
  exclude_selectors: string[]
  headings: { id: string; text: string }[]
  title_block: string | null
  links: { id: string; text: string; url: string | null }[]
  next_link: string | null
  previous_link: string | null
  number_from: 'title' | 'url'
}

export interface ExtractionImage {
  id: number
  display_url: string | null
  attr: string
  width: number
  height: number
  role: string
  // Reading order from 1; 0 = not a page.
  page: number
  reason: string
  has_image: boolean
}

export interface ExtractionComic {
  images: ExtractionImage[]
  roles: string[]
  page_count: number
}

// GET /api/sources/dramas/{id}/extraction
export interface ExtractionReview {
  kind: 'extraction_review'
  drama_id: number
  revision: string
  content_type: 'novel' | 'comic'
  why: ReviewWhy | string
  display_url: string | null
  confidence: { overall: { bucket: string | null; score: number }; fields: FieldConfidence[] }
  report: {
    headline: string
    lines: string[]
    llm_calls: number
    cache_hit: boolean
    profile: string
    pending_profile: { bucket: string | null } | null
  }
  can_save_profile: boolean
  novel: ExtractionNovel | null
  comic: ExtractionComic | null
}

export interface NovelRerunRequest {
  revision: string
  content_selector: string
  exclude_selectors: string[]
  title_block: string | null
  next_link: string | null
  previous_link: string | null
  number_from: 'title' | 'url'
}

export interface ImageChoice {
  id: number
  role: string
  page: number
}

export interface ProfileSaved {
  domain: string
  kind: string
  version: number
  replaces: number | null
}

// The review import job's result (sourceimport_<drama_id>).
export interface ReviewImportResult {
  kind: 'review_import'
  content_type: 'novel' | 'comic'
  char_count?: number
  pages_added?: number
}
