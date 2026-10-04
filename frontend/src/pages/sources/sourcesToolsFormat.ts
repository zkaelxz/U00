// Pure helpers for the Sources tools (SO02 site check, SO03 pasted page
// source, SO08 identify media, SO16 pasted-URL diagnostics) and the
// Discover pasted listing (DI07).
import { sentenceCase } from '../../labels'
import { MAX_PASTED_HTML_BYTES, MAX_PASTED_LISTING_CHARS, utf8Bytes } from '../../api/sourcesTools'
import type { MediaResource, SourceExtraction, UrlPreflight } from '../../types/sourcesTools'

/** Why pasted page source can't be sent yet, or null. */
export function pastedHtmlProblem(html: string): string | null {
  if (!html.trim()) return 'Paste the page source first.'
  if (utf8Bytes(html) > MAX_PASTED_HTML_BYTES) return 'That is more than 5 MB; copy just the page source.'
  return null
}

/** Why pasted listing text can't be sent yet, or null. */
export function pastedListingProblem(text: string): string | null {
  if (!text.trim()) return 'Paste the listing text first.'
  if (text.length > MAX_PASTED_LISTING_CHARS)
    return `That is more than ${MAX_PASTED_LISTING_CHARS.toLocaleString('en-US')} characters; paste one page at a time.`
  return null
}

type Tone = 'ok' | 'warn' | 'danger'

export function preflightTone(p: UrlPreflight): Tone {
  if (!p.permitted) return 'danger'
  return p.ok ? (p.warnings.length ? 'warn' : 'ok') : 'warn'
}

/** Short facts under the verdict: type, how it was reached, what was read. */
export function preflightFacts(p: UrlPreflight): string[] {
  const out: string[] = []
  if (p.content_type && p.content_type !== 'unknown') out.push(sentenceCase(p.content_type))
  if (p.tier) out.push(`via ${p.tier.replace(/_/g, ' ').toLowerCase()}`)
  if (p.text_chars) out.push(`${p.text_chars.toLocaleString('en-US')} characters`)
  if (p.images) out.push(`${p.images} image${p.images === 1 ? '' : 's'}`)
  if (p.confidence) out.push(`confidence ${p.confidence.toLowerCase()}`)
  return out
}

const ROLE_TEXT: Record<string, string> = {
  main: 'Main',
  alternate: 'Alternate',
  subtitle: 'Subtitle',
  audio_track: 'Audio track',
  trailer: 'Trailer',
  ad: 'Ad',
  preview: 'Preview',
  unrelated: 'Other',
}

export function resourceLabel(r: MediaResource): string {
  const role = ROLE_TEXT[r.role] ?? (r.role || 'Other')
  const bits = [role, r.kind]
  if (r.language) bits.push(r.language)
  if (r.label) bits.push(r.label)
  return bits.join(' · ')
}

/** Resources the video download can take, the main one first. */
export function playableResources(rs: MediaResource[]): MediaResource[] {
  const list = rs.filter((r) => r.downloadable)
  return [...list.filter((r) => r.role === 'main'), ...list.filter((r) => r.role !== 'main')]
}

export function extractionMeta(a: SourceExtraction): string {
  const bits: string[] = []
  if (a.content_type) bits.push(sentenceCase(a.content_type))
  bits.push(`AI calls: ${a.llm_calls}${a.cache_hit ? ' (cached result reused)' : ''}`)
  if (a.confidence) bits.push(`confidence ${a.confidence.toLowerCase()}`)
  return bits.join(' · ')
}

export function extractionAccess(a: SourceExtraction): string[] {
  if (!a.access) return []
  const out = [
    `Authentication: ${a.access.authentication ?? 'unknown'}`,
    `Entitlement/purchase: ${a.access.entitlement ?? 'unknown'}`,
    `Resource types found: ${a.resource_types.length ? a.resource_types.join(', ') : 'none identified'}`,
    `Technical protection: ${a.access.technical_protection ?? 'unknown'}`,
  ]
  return out.concat(a.access.protection_detail)
}

export function whenText(ts: number | null, now = Date.now()): string {
  if (!ts) return ''
  const mins = Math.round((now - ts * 1000) / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins} min ago`
  const hours = Math.round(mins / 60)
  if (hours < 48) return `${hours} h ago`
  return new Date(ts * 1000).toISOString().slice(0, 10)
}
