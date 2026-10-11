// Pure logic and copy for the Sources page.
// No React here, so everything is unit-tested in sourcesFormat.test.ts.
import { safeDetail } from '../../components/errorMessages'
import { humanizeValue } from '../../components/labels'
import type {
  SourceDetail,
  SourceErrorView,
  SourceHealth,
  SourcePace,
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

/** "2026-10-08" for a unix time in seconds. */
export function isoDay(ts: number): string {
  return new Date(ts * 1000).toISOString().slice(0, 10)
}

type ExtensionMark = Pick<SourceDetail, 'extension_only' | 'extension_marked_at' | 'extension_works_without'>

/** Marked, and no Static or Browser test has passed since: the extension is the way in. */
export function extensionOnlyNow(d: ExtensionMark): boolean {
  return !!d.extension_only && !d.extension_works_without
}

export const EXTENSION_ONLY_HINT = 'This now works without the extension: clear the marker?'

export function statusLabel(d: SourceDetail): string {
  return extensionOnlyNow(d) ? 'Extension only' : humanizeValue(d.status)
}

export function accessMethodLabel(d: SourceDetail): string {
  return extensionOnlyNow(d) ? 'Browser extension' : humanizeValue(d.access_method)
}

/** One line per tier. The marker only replaces the "You in a browser" line, and only while it is untested. */
export function tierLines(tiers: Record<string, SourceTierResult>, mark?: ExtensionMark): string[] {
  return Object.entries(tiers).map(([key, t]) => {
    const label = TIER_LABELS[key] ?? humanizeValue(key)
    if (key === 'USER_ASSISTED_BROWSER' && mark?.extension_only && !t.tested) {
      return `${label}: works (marked by you${mark.extension_marked_at ? `, ${isoDay(mark.extension_marked_at)}` : ''})`
    }
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

export const PACE_LABELS: Record<SourcePace, string> = { careful: 'Careful', normal: 'Normal', fast: 'Fast' }

/** The one line under a source's Pace selector. */
export function paceHelp(s: Pick<SourceSummary, 'pace' | 'fast_allowed' | 'slowed_down'>): string {
  if (s.slowed_down) return 'Slowed down: this site asked us to wait. It eases off by itself.'
  if (!s.fast_allowed) return "Fast is off: this site's rules haven't been checked."
  return s.pace === 'careful' ? 'Careful: slower, with more breaks.' : ''
}
