// Mirrors api/sources_extraction_schemas.py and the job results of
// services/sources_import_service.py (Sources parity SO09, SO06): the
// pasted-URL AI fallback and the comic import. Names only; no key ever
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
}
