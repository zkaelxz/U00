// Mirrors api/schemas/sources.py: the Sources registry and the Sources
// search/series jobs. No proxy URL, path, key or
// query string is ever part of these shapes.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export type SourcePace = 'careful' | 'normal' | 'fast'

export type SourceSummary = Schemas['SourceSummary']

export type SourceHealth = Schemas['SourceHealth']

export type SourceTierResult = Schemas['SourceTierResult']

export interface SourceDetail extends SourceSummary {
  status: string
  technical_status: string
  access_method: string | null
  content_access_status: string
  authentication_required: string
  purchase_required: string
  technical_protection: string
  automation_permission: string
  ai_ml_use: string
  tiers: Record<string, SourceTierResult>
  technical: Record<string, unknown>
  // Recorded findings, information only (enforcement is off).
  terms: Record<string, unknown>
  terms_enforced: boolean
  health_detail: SourceHealth
  extension_marked_at?: number | null
  extension_note?: string
  // A Static or Browser test passed after the marker was set.
  extension_works_without?: boolean
}

export interface ExtensionOnlyMark {
  extension_only: boolean
  extension_marked_at: number | null
  extension_note: string
  extension_works_without: boolean
}

export type SourceAttempt = Schemas['SourceAttempt']

export type SourceCacheStats = Schemas['SourceCacheStats']

export type SourcesSettings = Schemas['SourcesSettings']

// The whitelisted, partial POST /api/sources/settings body (extra=forbid).
export type SourcesSettingsUpdate = Partial<
  Pick<
    SourcesSettings,
    | 'pace_min_delay'
    | 'pace_max_delay'
    | 'max_concurrent'
    | 'max_retries'
    | 'session_break_min_requests'
    | 'session_break_max_requests'
    | 'session_break_min_delay'
    | 'session_break_max_delay'
    | 'cache_mode'
    | 'cache_max_mb'
    | 'check_interval_hours'
    | 'auto_queue_new_chapters'
    | 'demo_source_enabled'
    | 'extraction_diagnostics'
  >
>

export type SourceProfileVersion = Schemas['SourceProfileVersion']

export type SourceProfileDomain = Schemas['SourceProfileDomain']

export type TrackedSeries = Schemas['TrackedSeries']

export type SourceMoveChapter = Schemas['SourceMoveChapter']

export type SourceMovePreview = Schemas['SourceMovePreview']

export type SourceNotification = Schemas['SourceNotification']

export type SourcesJobStarted = Schemas['SourcesJobStarted']

// A per-source failure inside a search result, or a failed job's error.
export interface SourceErrorView {
  status: number
  code?: string
  message: string
  details?: Record<string, unknown> | null
}

interface SearchEntry {
  source: string
  series_id: string
  title: string
  url: string
  cover_url: string // never rendered: loading it would bypass pacing
}

interface SearchMatch {
  title: string
  key: string
  sources: string[]
  entries: SearchEntry[]
}

export interface SearchResult {
  kind: 'search'
  query: string
  cancelled: boolean
  results: SearchMatch[]
  errors: Record<string, SourceErrorView>
  per_source_counts: Record<string, number>
}

// A download link a work's page posts (an EPUB on a file locker): shown for
// the person to open themselves; the app never fetches it. The URL has no
// query string, so an extraction code travels as `password`.
export interface SeriesLink {
  label: string
  url: string
  password: string
}

export interface SeriesInfo {
  title: string | null
  url: string | null
  cover_url: string | null // never rendered
  authors: string[]
  description: string | null
  genres: string[]
  status: string | null
  content_type: string | null
  language: string | null
  links?: SeriesLink[]
}

export interface SeriesChapter {
  chapter_id: string
  title: string
  group: string
  url: string
}

export interface SeriesResult {
  kind: 'series'
  source: string
  series_id: string
  info: SeriesInfo | null
  chapters: SeriesChapter[]
}

export interface SourcesJobResult<R = Record<string, unknown>> {
  job_id: string
  // 'idle': no such job has run in this API process (job_id is '').
  status: string | null
  progress: number | null
  message: string | null
  result: R | null // only once done
  // Series jobs only: which series the per-source run is for (also while running).
  source?: string | null
  series_id?: string | null
}

// What the page remembers about the open series (sources.lastSeries).
export interface OpenSeries {
  source: string
  series_id: string
  title: string
}

// "Check now" (sources_chapter_check). `errors` is keyed by series title.
export interface CheckResult {
  checked: number
  new: number
  errors: Record<string, string>
  queued: string[]
  // Series whose new chapters were saved as CBZ files.
  saved?: string[]
  skipped?: boolean
}

// PC-only jobs: sources_signin_<name> and sources_tiertest_<name>.
export type SourceTier = 'static' | 'browser' | 'signed_in'

export interface SigninResult {
  kind: 'signin'
  source: string
  ok: boolean
  message: string
  lines: string[]
  has_saved_signin: boolean
}

export interface TierTestResult {
  kind: 'tier_test'
  source: string
  tier: SourceTier
  ok: boolean
  reason: string | null
  detail: string | null
}

export type SourceSigninForgetResult = Schemas['SourceSigninForgetResult']
