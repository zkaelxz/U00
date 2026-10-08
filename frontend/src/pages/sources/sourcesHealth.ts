import { humanizeValue } from '../../components/labels'
import type { SourceHealth, SourceSummary } from '../../types/sources'
import { searchableSources } from './sourcesSearch'

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
