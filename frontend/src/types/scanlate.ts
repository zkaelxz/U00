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

export interface ScanlateNote {
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
}

export type ScanlateExportFormat = 'zip' | 'pdf'

export interface ScanlateJobStarted {
  job_id: string
  engine?: string | null
  mode?: string | null
}
