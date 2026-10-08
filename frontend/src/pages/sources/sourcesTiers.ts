import { humanizeValue } from '../../components/labels'
import type { SourceTier, SourceTierResult, TierTestResult } from '../../types/sources'

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
  const why = r.reason ? humanizeValue(r.reason).toLowerCase() : 'failed'
  return `${label}: ${why}${r.detail ? ` (${r.detail})` : ''}.`
}
