// Mirrors api/schemas.py: the Sources registry (Slice 56) and the Sources
// search/series jobs (API batch 1, spec S-3). No proxy URL, path, key or
// query string is ever part of these shapes.

export interface SourceSupports {
  search: boolean
  get_series: boolean
  get_chapters: boolean
  get_pages: boolean
  download_page: boolean
  get_chapter_text: boolean
  get_audio_url: boolean
  login: boolean
}

export type HealthLight = 'green' | 'yellow' | 'red'

export interface SourceSummary {
  name: string
  display_name: string
  content_types: string[]
  languages: string[]
  supports: SourceSupports
  import_supported: boolean
  auth_supported: boolean
  supports_adult_toggle: boolean
  enabled: boolean
  adult_enabled: boolean
  health: HealthLight | string
  has_saved_signin: boolean
}

export interface SourceHealth {
  light: HealthLight | string
  consecutive_failures: number
  last_success: number | null
  last_failure: number | null
  last_error_type: string | null
  last_error: string | null
  last_latency: number | null
  unavailable_until: number | null
  retry_after: number | null
}

export interface SourceTierResult {
  tested: boolean
  ok: boolean
  reason: string | null
  detail: string | null
  at: number | null
}

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
}

export interface SourceAttempt {
  url: string // scheme+host+path only
  created_at: number | null
  tier: string | null
  test_now: boolean
  ok: boolean | null
  technical_status: string | null
  capability_status: string | null
  reasons: string[]
  lines: string[]
  handoff: Record<string, unknown> | null
}

export interface SourceCacheStats {
  entries: number
  bytes: number
}

export interface SourcesSettings {
  pace_min_delay: number
  pace_max_delay: number
  max_concurrent: number
  max_retries: number
  session_break_min_requests: number
  session_break_max_requests: number
  session_break_min_delay: number
  session_break_max_delay: number
  cache_mode: string
  check_interval_hours: number
  auto_queue_new_chapters: boolean
  demo_source_enabled: boolean
  extraction_diagnostics: boolean
  proxy_configured: boolean
  cache_modes: string[]
  cache: SourceCacheStats
}

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
    | 'check_interval_hours'
    | 'auto_queue_new_chapters'
    | 'demo_source_enabled'
    | 'extraction_diagnostics'
  >
>

export interface SourceProfileVersion {
  version: number | null
  kind: string | null
  status: string | null
  origin: string | null
  created_at: number | null
  approved: boolean
  failures: number
  last_failure_reason: string | null
  last_used: number | null
}

export interface SourceProfileDomain {
  domain: string
  versions: SourceProfileVersion[]
}

export interface TrackedSeries {
  source: string
  series_id: string
  title: string
  url: string
  drama_id: number | null
  last_checked: number | null
  last_check_error: string | null
}

export interface SourceNotification {
  id: number
  source: string
  series_id: string
  chapter_id: string
  title: string | null
  created_at: number
  dismissed: boolean
}

export interface SourcesJobStarted {
  job_id: string
}

// A per-source failure inside a search result, or a failed job's error.
export interface SourceErrorView {
  status: number
  code?: string
  message: string
  details?: Record<string, unknown> | null
}

export interface SearchEntry {
  source: string
  series_id: string
  title: string
  url: string
  cover_url: string // never rendered: loading it would bypass pacing
}

export interface SearchMatch {
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

export interface SourceSigninForgetResult {
  source: string
  forgotten: boolean
  has_saved_signin: boolean
}
