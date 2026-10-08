// Pure logic and copy for the Sources page.
// No React here, so everything is unit-tested in sourcesFormat.test.ts.
import { ApiError } from '../../api/client'
import { safeDetail } from '../../components/errorMessages'
import type { DramaSummary } from '../../api/types'
import { humanize, humanizeValue } from '../../components/labels'
import type {
  CheckResult,
  SeriesChapter,
  OpenSeries,
  SeriesInfo,
  SeriesLink,
  SeriesResult,
  SourceErrorView,
  SourceHealth,
  SourcesSettings,
  SourcesSettingsUpdate,
  SourceSummary,
  SourceTier,
  SourceTierResult,
  TierTestResult,
} from '../../types/sources'

// While false, other devices cannot search sources from the UI (search stays
// PC only). The server's permissions (docs/remote-access-decision.md) still
// decide what a request may do; this is the owner's call on the UI side.
export const SEARCH_REMOTE_ALLOWED = false

export const RESULTS_PAGE = 30
export const CHAPTERS_PAGE = 100

// ---------------------------------------------------------------- search

/** Identifies the "Open on …" button that opened a series (focus returns to it). */
export const openerKey = (source: string, seriesId: string) => `${source}:${seriesId}`

export function looksLikeUrl(text: string): boolean {
  const t = text.trim().toLowerCase()
  return t.includes('://') || t.startsWith('www.')
}

/** Enabled sources that can search, in list order. */
export function searchableSources(sources: SourceSummary[]): SourceSummary[] {
  return sources.filter((s) => s.enabled && s.supports.search)
}

/**
 * The checked sources: every searchable one except those the viewer unticked.
 * The remembered list holds unticked names, so a newly enabled source starts
 * ticked and a name that is no longer enabled simply drops out.
 */
export function selectedSources(searchable: string[], excluded: string[]): string[] {
  return searchable.filter((n) => !excluded.includes(n))
}

/** The `sources` field to send: left out when every searchable source is ticked. */
export function searchSourcesParam(searchable: string[], selected: string[]): string[] | undefined {
  return selected.length === searchable.length ? undefined : selected
}

export function searchInSummary(selected: number, total: number): string {
  const noun = `searchable source${total === 1 ? '' : 's'}`
  if (selected === total) return `All ${total} ${noun}`
  return `${selected} of ${total} ${noun}`
}

/** Why Search is disabled, or null when it can run. */
export function searchDisabledReason(
  query: string,
  searchable: number,
  selected: number,
  remote: boolean,
): string | null {
  if (!query.trim()) return 'Still needed: a title.'
  if (looksLikeUrl(query)) return 'Enter a title, not a link.'
  if (searchable === 0) {
    return `Still needed: a source that can search. Turn one on in Source settings${remote ? ' on the main PC' : ''}.`
  }
  if (selected === 0) return 'Still needed: at least one source.'
  return null
}

export function resultsHeader(results: number, problems: number): string {
  const r = `${results} result${results === 1 ? '' : 's'}`
  if (!problems) return r
  return `${r} · ${problems} source${problems === 1 ? '' : 's'} had problems`
}

export function percent(progress: number | null | undefined): string {
  return progress === null || progress === undefined ? '' : ` ${Math.round(progress * 100)}%`
}

// ---------------------------------------------------------------- errors

export interface SourceErrorCopy {
  text: string
  // A browser check: the page to open yourself (scheme+host+path).
  openUrl?: string
  // Worth offering Try again (a browser check the user may have cleared).
  retry?: boolean
}

type ErrLike = Partial<SourceErrorView> & { details?: unknown }

function detailsOf(err: ErrLike): Record<string, unknown> {
  const d = err.details
  return d && typeof d === 'object' ? (d as Record<string, unknown>) : {}
}

/** Plain-English copy for a failed source call (an ApiError or a search's per-source error). */
export function describeSourceError(err: unknown, source: string, remote = false): SourceErrorCopy {
  const e = (err ?? {}) as ErrLike
  const d = detailsOf(e)
  const reason = d.reason
  if (reason === 'CONTENT_HIDDEN') {
    return { text: `${source} hides some works. Turn on Adult works in Source settings${remote ? ' on the main PC' : ''}.` }
  }
  if (reason === 'TOS_PROHIBITED') return { text: `${source}'s terms restrict automated access.` }
  if (reason === 'NOT_SUPPORTED') return { text: `${source} can't do that.` }
  if (reason === 'CANCELLED') return { text: 'Stopped.' }
  if (e.status === 409 && d.handoff) {
    const url = typeof d.open_url === 'string' && /^https?:\/\//i.test(d.open_url) ? d.open_url : undefined
    return { text: `${source} showed a browser check. Baihe never gets past these.`, openUrl: url, retry: true }
  }
  if (e.status === 503) {
    const after = typeof d.retry_after === 'number' ? d.retry_after : 0
    if (after > 0) {
      return {
        text: `${source} is paused after repeated failures. Try again in ${Math.ceil(after / 60)} min.`,
      }
    }
    return { text: `${source} isn't reachable right now.` }
  }
  const detail = typeof e.message === 'string' ? safeDetail(e.message) : null
  return { text: detail ?? `${source} failed. Details are in the app log.` }
}

// ---------------------------------------------------------------- header

/** Page-head meta: "3 sources on · 2 searchable"; paused (red) is shown apart, as a warn badge. */
export function pageSummary(sources: SourceSummary[]): { on: string; paused: number } {
  const on = sources.filter((s) => s.enabled).length
  const searchable = searchableSources(sources).length
  const paused = sources.filter((s) => s.health === 'red').length
  return { on: `${on} source${on === 1 ? '' : 's'} on · ${searchable} searchable`, paused }
}

export function healthTone(light: string): 'ok' | 'warn' | 'bad' {
  return light === 'green' ? 'ok' : light === 'yellow' ? 'warn' : 'bad'
}

export function healthText(light: string): string {
  if (light === 'green') return 'OK'
  if (light === 'yellow') return 'Failing'
  if (light === 'red') return 'Paused'
  return humanizeValue(light)
}

// ---------------------------------------------------------------- series

export function seriesMeta(display: string, info: SeriesInfo | null, chapters: number): string {
  const parts = [display, `${chapters} chapter${chapters === 1 ? '' : 's'}`]
  if (info?.status) parts.push(humanizeValue(info.status))
  if (info?.language) parts.push(humanize('language', info.language))
  return parts.join(' · ')
}

/** A work's posted download links that are safe to render as links. */
export function seriesLinks(info: SeriesInfo | null): SeriesLink[] {
  return (info?.links ?? []).filter((l) => !!safeHref(l.url)).map((l) => ({ ...l, label: l.label || l.url }))
}

export function seriesExtra(info: SeriesInfo | null): string {
  if (!info) return ''
  return [...(info.authors ?? []), ...(info.genres ?? [])].filter(Boolean).join(' · ')
}

type ChapterGroup = { group: string; chapters: SeriesChapter[] }

/** Chapters by `group`, groups in first-seen order. */
export function groupChapters(chapters: SeriesChapter[]): ChapterGroup[] {
  const out: ChapterGroup[] = []
  for (const c of chapters) {
    const g = c.group ?? ''
    const found = out.find((o) => o.group === g)
    if (found) found.chapters.push(c)
    else out.push({ group: g, chapters: [c] })
  }
  return out
}

/** The first `n` chapters across the groups (empty groups dropped). */
export function limitGroups(groups: ChapterGroup[], n: number): ChapterGroup[] {
  const out: ChapterGroup[] = []
  let left = n
  for (const g of groups) {
    if (left <= 0) break
    const rows = g.chapters.slice(0, left)
    left -= rows.length
    out.push({ group: g.group, chapters: rows })
  }
  return out
}

/** An http(s) link, or null (never render another scheme as a link). */
export function safeHref(url: string | null | undefined): string | null {
  return url && /^https?:\/\//i.test(url) ? url : null
}

/** True when a start's 409 names this same job id as already running. */
export function isSameJobConflict(e: unknown, jobId: string): boolean {
  if (!(e instanceof ApiError) || e.status !== 409) return false
  const d = e.details as { job_id?: unknown } | null | undefined
  return !!d && d.job_id === jobId
}

export interface SeriesJobLike {
  status: 'idle' | 'running' | 'done' | 'error'
  result: SeriesResult | null
  error: unknown
  startedHere: boolean
  startError: unknown
  // Which series a running run is for, when the server says (series jobs).
  runningFor?: { source: string; series_id: string } | null
}

export interface SeriesView {
  status: 'idle' | 'running' | 'done' | 'error'
  result: SeriesResult | null
  error: unknown
  // Another series from this source is loading: a start was refused, or
  // the running run (found on load) reports a different series.
  busyOther: boolean
}

/**
 * What the series panel may show for the open series. The job id is per
 * source, so the job can hold a different series: a finished result counts
 * only when its source and series_id match `open`, and a run found on mount
 * (not started here) that failed is not shown, since it can't be matched.
 * A running run that reports another series (`runningFor`) is `busyOther`.
 */
export function seriesView(open: OpenSeries | null, job: SeriesJobLike, jobId: string | null): SeriesView {
  const busyOther = !!jobId && isSameJobConflict(job.startError, jobId)
  const none: SeriesView = { status: 'idle', result: null, error: null, busyOther }
  if (!open || busyOther) return none
  if (job.status === 'done') {
    const r = job.result
    return r && r.source === open.source && r.series_id === open.series_id
      ? { status: 'done', result: r, error: null, busyOther }
      : none
  }
  if (job.status === 'error') return job.startedHere ? { status: 'error', result: null, error: job.error, busyOther } : none
  const f = job.status === 'running' ? job.runningFor : null
  if (f && (f.source !== open.source || f.series_id !== open.series_id)) {
    // Found running on load for another series of this source.
    return { ...none, busyOther: true }
  }
  return { status: job.status, result: null, error: null, busyOther }
}

// ---------------------------------------------------------------- time

/** "just now", "5 min ago", "2 h ago", "3 d ago" for a unix time in seconds. */
export function ago(ts: number | null | undefined, nowMs = Date.now()): string {
  if (!ts) return 'never'
  const s = Math.max(0, nowMs / 1000 - ts)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`
  return `${Math.floor(s / 86400)} d ago`
}

export function isoTime(ts: number | null | undefined): string | undefined {
  return ts ? new Date(ts * 1000).toISOString() : undefined
}

// ---------------------------------------------------------------- detail

const TIER_LABELS: Record<string, string> = {
  STATIC_HTTP: 'Static',
  RENDERED_BROWSER: 'Browser',
  AUTHENTICATED_BROWSER: 'Signed-in',
  USER_ASSISTED_BROWSER: 'You in a browser',
  OFFICIAL_API: 'Official API',
}

export function tierLines(tiers: Record<string, SourceTierResult>): string[] {
  return Object.entries(tiers).map(([key, t]) => {
    const label = TIER_LABELS[key] ?? humanizeValue(key)
    if (!t.tested) return `${label}: untested`
    if (t.ok) return `${label}: works`
    const why = t.reason ? humanizeValue(t.reason).toLowerCase() : ''
    return `${label}: failed${why ? ` (${why})` : ''}`
  })
}

const ERROR_CATEGORY_LABELS: Record<string, string> = {
  blocked: 'blocked',
  site_down: 'site down',
  page_missing: 'page missing',
  layout_changed: 'layout changed',
  slow: 'slow',
  needs_sign_in: 'needs sign-in',
  domains_unreachable: 'every known address is unreachable',
  other: 'failed',
}

/** Plain-language reason for the last failure; the raw error type when the server sent no category. */
export function errorCategoryLabel(h: SourceHealth): string | null {
  if (h.last_error_category) return ERROR_CATEGORY_LABELS[h.last_error_category] ?? 'failed'
  return h.last_error_type || null
}

/** Hover text for the health line: the raw error type, kept for support. */
export function healthTooltip(h: SourceHealth): string | undefined {
  return h.last_error_type ? `Error type: ${h.last_error_type}` : undefined
}

export function healthLine(h: SourceHealth, nowMs = Date.now()): string {
  const parts: string[] = []
  if (h.last_success) parts.push(`Last success ${ago(h.last_success, nowMs)}`)
  if (h.last_failure) {
    parts.push(`last failure ${ago(h.last_failure, nowMs)}${errorCategoryLabel(h) ? ` (${errorCategoryLabel(h)})` : ''}`)
  }
  if (h.last_latency !== null && h.last_latency !== undefined) parts.push(`${h.last_latency.toFixed(1)} s`)
  if (!parts.length) return 'No requests yet.'
  const line = parts.join(' · ')
  return line.charAt(0).toUpperCase() + line.slice(1)
}

export function pausedFor(retryAfter: number | null | undefined): string | null {
  if (!retryAfter || retryAfter <= 0) return null
  return `Paused for another ${Math.ceil(retryAfter / 60)} min.`
}

// ---------------------------------------------------------------- settings

const CACHE_LABELS: Record<string, string> = {
  none: 'Keep nothing',
  temporary: 'Temporary',
  keep_originals: 'Keep originals',
  keep_translated: 'Keep translated only',
  keep_both: 'Keep both',
}

export const cacheLabel = (mode: string) => CACHE_LABELS[mode] ?? humanizeValue(mode)

const fmtNum = (n: number) => String(Number.isInteger(n) ? n : Number(n.toFixed(2)))

function gapText(s: Pick<SourcesSettings, 'pace_min_delay' | 'pace_max_delay'>): string {
  return `${fmtNum(s.pace_min_delay)}–${fmtNum(s.pace_max_delay)} s gap`
}

export function settingsSummary(s: SourcesSettings | null, sources: SourceSummary[]): string {
  const on = `${sources.filter((x) => x.enabled).length} of ${sources.length} sources on`
  if (!s) return on
  return `${on} · ${gapText(s)} · cache: ${cacheLabel(s.cache_mode)}`
}

export function pacingSummary(s: SourcesSettings): string {
  const breaks =
    s.session_break_min_requests > 0 && s.session_break_max_requests > 0
      ? `breaks every ${s.session_break_min_requests}–${s.session_break_max_requests}`
      : 'breaks off'
  return [
    gapText(s),
    `${s.max_concurrent} at a time`,
    `${s.max_retries} ${s.max_retries === 1 ? 'retry' : 'retries'}`,
    breaks,
  ].join(' · ')
}

export type NumKey =
  | 'pace_min_delay'
  | 'pace_max_delay'
  | 'max_concurrent'
  | 'max_retries'
  | 'session_break_min_requests'
  | 'session_break_max_requests'
  | 'session_break_min_delay'
  | 'session_break_max_delay'
  | 'check_interval_hours'
  | 'cache_max_mb'

export type BoolKey = 'auto_queue_new_chapters' | 'demo_source_enabled' | 'extraction_diagnostics'

export interface NumField {
  key: NumKey
  label: string
  min: number
  max: number
  step?: number
  help?: string
  // The field this one must be at least (a max's min).
  atLeast?: NumKey
}

// Pairs in display order; ranges match services/sources_registry_service.py.
export const PACING_ROWS: NumField[][] = [
  [
    { key: 'pace_min_delay', label: 'Gap min (s)', min: 3, max: 60, step: 0.5,
      help: "Seconds between requests to one source. Can't go below 3." },
    { key: 'pace_max_delay', label: 'Gap max (s)', min: 0, max: 120, step: 0.5, atLeast: 'pace_min_delay' },
  ],
  [
    { key: 'max_concurrent', label: 'At once', min: 1, max: 4,
      help: '1 is human-paced. A site can still insist on slower.' },
    { key: 'max_retries', label: 'Retries', min: 0, max: 6,
      help: 'For busy or overloaded replies only; never for a browser check.' },
  ],
  [
    { key: 'session_break_min_requests', label: 'Break every (requests)', min: 0, max: 200,
      help: '0 = off. Picked at random between the two numbers.' },
    { key: 'session_break_max_requests', label: '…up to', min: 0, max: 200, atLeast: 'session_break_min_requests' },
  ],
  [
    { key: 'session_break_min_delay', label: 'Break min (s)', min: 0, max: 600, step: 5 },
    { key: 'session_break_max_delay', label: 'Break max (s)', min: 0, max: 900, step: 5, atLeast: 'session_break_min_delay' },
  ],
]

export const CHECK_FIELD: NumField = { key: 'check_interval_hours', label: 'Check tracked (h)', min: 0, max: 168, help: '0 = off.' }

export const CACHE_MAX_FIELD: NumField = {
  key: 'cache_max_mb', label: 'Cache limit (MB)', min: 0, max: 1_000_000,
  help: '0 = no limit. Oldest-used pages go first.',
}

const NUM_FIELDS: NumField[] = [...PACING_ROWS.flat(), CHECK_FIELD, CACHE_MAX_FIELD]

export const BOOL_FIELDS: { key: BoolKey; label: string; help?: string }[] = [
  { key: 'auto_queue_new_chapters', label: 'Auto-import new chapters', help: 'Off: new chapters are announced, not downloaded.' },
  { key: 'demo_source_enabled', label: 'Show demo source' },
  { key: 'extraction_diagnostics', label: 'Extraction diagnostics' },
]

// The editable form: numbers as typed text so a half-typed value is allowed.
export type PacingDraft = Record<NumKey, string> & Record<BoolKey, boolean> & { cache_mode: string }

export function draftFrom(s: SourcesSettings): PacingDraft {
  const d = { cache_mode: s.cache_mode } as PacingDraft
  for (const f of NUM_FIELDS) d[f.key] = String(s[f.key])
  for (const b of BOOL_FIELDS) d[b.key] = s[b.key]
  return d
}

const asNumber = (text: string) => (text.trim() === '' ? NaN : Number(text))

/** Field errors: out of range, not a number, or a max below its min. */
export function pacingErrors(d: PacingDraft): Partial<Record<NumKey, string>> {
  const out: Partial<Record<NumKey, string>> = {}
  for (const f of NUM_FIELDS) {
    const v = asNumber(d[f.key])
    if (!Number.isFinite(v)) out[f.key] = 'Enter a number.'
    else if (v < f.min || v > f.max) out[f.key] = `Must be between ${f.min} and ${f.max}.`
    else if (!f.step && !Number.isInteger(v)) out[f.key] = 'Enter a whole number.'
  }
  for (const f of NUM_FIELDS) {
    if (!f.atLeast || out[f.key] || out[f.atLeast]) continue
    if (asNumber(d[f.key]) < asNumber(d[f.atLeast])) out[f.key] = 'Must be at least the min.'
  }
  return out
}

/** Only the keys that differ from what the server sent. */
export function settingsChanges(original: SourcesSettings, d: PacingDraft): SourcesSettingsUpdate {
  const out: SourcesSettingsUpdate = {}
  for (const f of NUM_FIELDS) {
    const v = asNumber(d[f.key])
    if (Number.isFinite(v) && v !== original[f.key]) out[f.key] = v
  }
  for (const b of BOOL_FIELDS) if (d[b.key] !== original[b.key]) out[b.key] = d[b.key]
  if (d.cache_mode !== original.cache_mode) out.cache_mode = d.cache_mode
  return out
}

export function profileLine(v: {
  version: number | null
  kind: string | null
  status: string | null
  origin: string | null
  failures: number
  last_failure_reason: string | null
}): string {
  const parts = [`v${v.version ?? '?'}`]
  if (v.kind) parts.push(v.kind)
  if (v.status) parts.push(v.status)
  if (v.origin) parts.push(`from ${v.origin}`)
  if (v.failures) {
    parts.push(`${v.failures} failure${v.failures === 1 ? '' : 's'}${v.last_failure_reason ? ` (${v.last_failure_reason})` : ''}`)
  }
  return parts.join(' · ')
}

// "Check now" (sources_chapter_check): one line for the finished run.
export function checkSummary(r: CheckResult): string {
  if (r.skipped) return 'Another check was already running, so this one checked nothing.'
  const parts = [`Checked ${r.checked} series`]
  parts.push(r.new ? `${r.new} new chapter${r.new === 1 ? '' : 's'}` : 'no new chapters')
  if (r.queued.length) parts.push(`importing into ${r.queued.length} drama${r.queued.length === 1 ? '' : 's'}`)
  if (r.saved?.length) parts.push(`saved ${r.saved.length} series as CBZ`)
  const failed = Object.keys(r.errors).length
  if (failed) parts.push(`${failed} failed`)
  return parts.join(' · ') + '.'
}

// Which dramas a tracked series can auto-import into (same rule as an import).
export function trackedDramaChoices(dramas: DramaSummary[], source: SourceSummary | undefined): DramaSummary[] {
  if (!source) return []
  const comic = source.supports.get_pages
  const ok = comic ? ['manhua', 'manga', 'manhwa'] : ['novel']
  return dramas.filter((d) => ok.includes((d.media_type ?? '').toLowerCase()))
}

// The per-tier "Test now" buttons, in ladder order.
export const TIER_TESTS: { tier: SourceTier; key: string }[] = [
  { tier: 'static', key: 'STATIC_HTTP' },
  { tier: 'browser', key: 'RENDERED_BROWSER' },
  { tier: 'signed_in', key: 'AUTHENTICATED_BROWSER' },
]
export const tierLabel = (tier: SourceTier) => TIER_LABELS[TIER_TESTS.find((t) => t.tier === tier)!.key]

export function tierTestLine(r: TierTestResult): string {
  const label = tierLabel(r.tier)
  if (r.ok) return `${label}: works.`
  // The detail already says what to install; "not installed" would read as if the browser were missing.
  if (r.reason === 'NOT_INSTALLED' && r.detail) return `${label}: ${r.detail.replace(/\.$/, '')}.`
  const why = r.reason ? humanizeValue(r.reason).toLowerCase() : 'failed'
  return `${label}: ${why}${r.detail ? ` (${r.detail})` : ''}.`
}

// A pasted page for sign-in / tests: a plain http(s) URL (the server checks the site).
export function pageUrlProblem(text: string): string | null {
  const t = text.trim()
  if (!t) return null
  try {
    const u = new URL(t)
    return u.protocol === 'http:' || u.protocol === 'https:' ? null : 'Use an http:// or https:// address.'
  } catch {
    return 'Paste a full address, starting with https://.'
  }
}

// The proxy field: "" clears it; otherwise http(s)://host[:port], nothing after.
export function proxyProblem(text: string): string | null {
  const t = text.trim()
  if (!t) return null
  try {
    const u = new URL(t)
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return 'HTTP(S) proxies only.'
    if (!u.hostname || (u.pathname !== '/' && u.pathname !== '') || u.search || u.hash) {
      return 'Just the address and port, e.g. http://127.0.0.1:8080.'
    }
    return null
  } catch {
    return 'Use an address like http://127.0.0.1:8080.'
  }
}
