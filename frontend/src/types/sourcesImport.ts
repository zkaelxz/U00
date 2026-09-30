// Mirrors the S-4/S-5 import contract (docs spec s4-s5-url-import §2): the
// paste-a-URL preview (R1), the novel-text URL import (R2), the chapter
// import (R3), tracking (R4, existing TrackedSeries shape) and the Workspace
// video-URL download (R5). Server text is scrubbed; URLs are scheme+host+path.

export type UrlContentType = 'video' | 'novel' | 'comic' | 'unknown'
export type UrlRoute = 'series' | 'chapter' | 'video' | 'page'

// R1 result (GET /api/sources/jobs/sources_url_preview/result, once done).
export interface UrlPreview {
  kind: 'url_preview'
  content_type: UrlContentType | string
  route: UrlRoute | string
  platform: string | null
  title: string | null
  chapter: string | null
  chapter_id: string | null
  language: string | null
  chapter_count: number | null
  // The source (adapter) name that can open the series, when there is one.
  adapter: string | null
  series_id: string | null
  text_length: number | null
  image_count: number | null
  notes: string[]
  display_url: string | null
}

// not_attempted (Step 107): the run stopped (cancel, browser check) before this chapter.
export type ChapterOutcome = 'imported' | 'skipped' | 'failed' | 'not_found' | 'not_attempted'

export interface ChapterImportRow {
  chapter_id: string
  title: string
  outcome: ChapterOutcome | string
  pages?: number | null
  chars?: number | null
  error?: string | null
}

// R3 result (job sourceimport_<drama_id>).
export interface ChapterImportResult {
  kind: 'chapter_import'
  chapters: ChapterImportRow[]
  imported_count: number
  skipped_count: number
  failed_count: number
  // Step 107: failed + not attempted ids (the retry set) and whether any exist.
  not_attempted_count: number
  retry_chapter_ids: string[]
  partial: boolean
  cancelled: boolean
  handoff: Record<string, unknown> | null
}

// R2 result (job sourceimport_<drama_id>). needs_review: nothing was written.
export interface UrlImportResult {
  kind: 'url_import'
  needs_review: boolean
  char_count: number
  // A Review extraction was opened for the drama (parity SO10).
  review_open?: boolean
}

export type SourceImportResult = ChapterImportResult | UrlImportResult

export interface ChapterImportRequest {
  series_id: string
  chapter_ids: string[]
  drama_id: number
}

export interface UrlDownloadRequest {
  url: string
  audio_only: boolean
  confirm_replace_audio: boolean
}

// Step 107: GET /api/sources/{name}/import-state?series_id=&drama_id= --
// which chapters of a series are already in a drama, and the ones the last
// imports left failed or not attempted ("Retry failed chapters (N)").
export interface ImportRetryRow {
  chapter_id: string
  title: string
  // partial: interrupted mid-write -- shown, never retried automatically
  status: 'failed' | 'not_attempted' | 'partial'
  error: string
}

export interface ImportState {
  source: string
  series_id: string
  drama_id: number
  imported_chapter_ids: string[]
  retry: ImportRetryRow[]
  retry_count: number
}
