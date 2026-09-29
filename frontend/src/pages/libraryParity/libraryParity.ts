import { humanize } from '../../components/labels'
import { workspaceHref } from '../../components/libraryView'
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
  // The unit is picked after rounding, so 999,950 is "1M", not "1000k".
  if (n >= 999_500) return `${(n / 1_000_000).toFixed(n >= 9_950_000 ? 0 : 1).replace(/\.0$/, '')}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(n >= 9_950 ? 0 : 1).replace(/\.0$/, '')}k`
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

// Parity L05: only a series with 2+ dramas is shown (one drama shares
// nothing), with a count per media type; a drama with no type is an audio
// drama, as in Streamlit.
export function sharedSeries<S extends { dramas: { media_type: string | null }[] }>(items: S[]): (S & { types: Record<string, number> })[] {
  return items.filter((s) => s.dramas.length >= 2).map((s) => {
    const types: Record<string, number> = {}
    for (const d of s.dramas) {
      const t = d.media_type || 'audio_drama'
      types[t] = (types[t] ?? 0) + 1
    }
    return { ...s, types }
  })
}

// Parity L05: "14 shared characters · 1 glossary term".
export const sharedLine = (s: { character_count: number; glossary_term_count: number }) =>
  `${plural(s.character_count, 'shared character')} · ${plural(s.glossary_term_count, 'glossary term')}`

// Parity P03: create, then land on the new drama's Source stage with
// "Auto-fill metadata" open. The flag rides in the hash's query, which the
// router ignores; the auto-fill panel reads it once and drops it.
const AUTOFILL_FLAG = 'autofill'

export const autofillHref = (id: number) => `${workspaceHref(id, 'source')}?${AUTOFILL_FLAG}=1`

const splitHash = (hash: string) => {
  const i = hash.indexOf('?')
  return i < 0 ? [hash, ''] : [hash.slice(0, i), hash.slice(i + 1)]
}

export const wantsAutofill = (hash: string) => new URLSearchParams(splitHash(hash)[1]).get(AUTOFILL_FLAG) === '1'

export function withoutAutofill(hash: string): string {
  const [path, qs] = splitHash(hash)
  const params = new URLSearchParams(qs)
  params.delete(AUTOFILL_FLAG)
  const rest = params.toString()
  return rest ? `${path}?${rest}` : path
}
