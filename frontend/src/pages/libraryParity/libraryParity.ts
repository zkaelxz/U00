import { humanize } from '../../components/labels'
import type { LibraryUsage } from '../../types/library'

// Parity L01/L04: the share of logged input tokens served from a provider's
// prompt cache (services/library_service.cache_hit_share).
export function cacheHitShare(u: Pick<LibraryUsage, 'input_tokens' | 'cache_read_tokens'>): number {
  return u.input_tokens ? (u.cache_read_tokens || 0) / u.input_tokens : 0
}

export const percent = (share: number) => `${Math.round(share * 100)}%`

const plural = (n: number, one: string) => `${n.toLocaleString()} ${one}${n === 1 ? '' : 's'}`

// Parity L01: "318 API calls · 30% cache hits"; the cache share only once
// input tokens have been logged.
export function usageLine(u: LibraryUsage): string {
  const parts = [plural(u.call_count, 'API call')]
  if (u.input_tokens) parts.push(`${percent(cacheHitShare(u))} cache hits`)
  return parts.join(' · ')
}

// Parity L01: "Transcribed 2 · Translated 1", in the API's order.
export function countsLine(counts: Record<string, number>, kind: 'status' | 'mediaType'): string {
  return Object.entries(counts).map(([k, n]) => `${humanize(kind, k)} ${n}`).join(' · ')
}

// Token counts in a narrow row: 540000 -> "540k", 1250000 -> "1.3M".
export function compactCount(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1).replace(/\.0$/, '')}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(n >= 10_000 ? 0 : 1).replace(/\.0$/, '')}k`
  return String(n)
}

// Parity L04: a free engine (or free tier) shows "$0.00 (free)", not a blank.
export const costLabel = (usd: number) => (usd === 0 ? '$0.00 (free)' : `$${usd.toFixed(2)}`)

// Parity L04: "540k in · 201k out · 30% cache hits · 212 calls".
export function costMeta(c: { input_tokens: number; output_tokens: number; cache_read_tokens: number; call_count: number }): string {
  return [
    `${compactCount(c.input_tokens)} in`,
    `${compactCount(c.output_tokens)} out`,
    `${percent(cacheHitShare(c))} cache hits`,
    plural(c.call_count, 'call'),
  ].join(' · ')
}
