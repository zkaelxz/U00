// Automatic Scanlate API shapes (api/scanlate_schemas.py). No path, file
// name or key ever comes back from these routes.

export interface ScanlateEngine {
  name: string
  label: string
  free: boolean
  key_configured: boolean
}

export interface ScanlateUploadLimits {
  image_types: string[]
  pdf: boolean
  max_image_mb: number
  max_image_megapixels: number
  max_pdf_mb: number
  max_pdf_pages: number
  max_files: number
  max_total_mb: number
  strip_slice_ratio: number
  slice_strips_default: boolean
}

export type ScanlateDetectBackend = 'auto' | 'cv' | 'ml'

export interface ScanlateConfig {
  drama_id: number
  source_language: string
  engines: ScanlateEngine[]
  default_engine: string
  detect_backends: ScanlateDetectBackend[]
  ml_weights_cached: boolean
  lama_weights_cached: boolean
  ocr_backend: string
  ocr_backend_installed: boolean
  page_count: number
  pages_with_regions: number
  pages_rendered: number
  job_id: string
  job_running: boolean
  upload_limits: ScanlateUploadLimits
}

interface ScanlateNote {
  level: 'info' | 'warning' | 'error' | string
  message: string
}

export interface ScanlatePageNotes {
  page_id: number
  ordinal: number
  notes: ScanlateNote[]
}

export interface ScanlateRunNotes {
  drama_id: number
  pages: ScanlatePageNotes[]
}

export interface ScanlateUploadResult {
  added: number
  page_ids: number[]
  pdf_pages_skipped: number
  strips_sliced: number
}

export type ScanlateRunMode = 'missing' | 'page' | 'all'

export interface ScanlateRunRequest {
  mode: ScanlateRunMode
  page_id?: number
  // Limits 'missing' / 'all' to one chapter (hidden pages are always skipped).
  chapter_id?: string
  confirm?: boolean
  engine?: string
  detect_backend?: ScanlateDetectBackend
  // Also translate large lettering drawn on the artwork (off unless set).
  art_captions?: boolean
}

export type ScanlateExportFormat = 'zip' | 'pdf'

export interface ScanlateJobStarted {
  job_id: string
  engine?: string | null
  mode?: string | null
}

export interface ScanlateRegion {
  id: number
  idx: number
  x: number
  y: number
  w: number
  h: number
  source_text: string
  translated_text: string
  font_size: number
  font_category: string
  kind: string
  // "#rrggbb" set in the editor; null lets the render choose.
  text_color: string | null
}

export interface ScanlatePageDetail {
  id: number
  drama_id: number
  ordinal: number
  width: number
  height: number
  rev: number
  undo_depth: number
  has_rendered: boolean
  regions: ScanlateRegion[]
}

// The fields a box edit may change; only the ones present count.
export type ScanlateRegionFields = Partial<
  Pick<ScanlateRegion, 'translated_text' | 'x' | 'y' | 'w' | 'h' | 'font_size' | 'font_category' | 'kind' | 'text_color'>
>

export interface ScanlateRegionEdit {
  expected_rev: number
  expected?: ScanlateRegionFields
  changes: ScanlateRegionFields
}

// A drawn box: only its rectangle; the server fills the rest (empty English, automatic colour).
export interface ScanlateRegionCreate {
  expected_rev: number
  region: { x: number; y: number; w: number; h: number }
}

export const MAX_REGION_TEXT_CHARS = 2000
export const MIN_FONT_SIZE = 6
export const MAX_FONT_SIZE = 200
