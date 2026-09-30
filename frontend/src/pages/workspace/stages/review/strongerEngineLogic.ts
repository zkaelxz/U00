// Step 99 "Try with a stronger engine": pure helpers for StrongerEngine.tsx.
// Suggest only (user decision 2026-09-29): nothing here calls an engine.
import { ApiError } from '../../../../api/client'
import { safeDetail } from '../../../../components/errorMessages'
import { humanize } from '../../../../components/labels'
import type { StrongerEngineSuggestions } from '../../../../types/strongerEngine'

/** What one row needs to offer the stronger engine. */
export interface StrongerOffer {
  engine: string
  engineLabel: string
  /** Plain labels, e.g. "Flagged in review · A glossary term isn't used". */
  caption: string
  estimateUsd: number
}

// 0 -> "free"; 0.0004 -> "under $0.001"; 0.0012 -> "$0.0012"; 1.5 -> "$1.50".
export function formatUsd(usd: number): string {
  if (!Number.isFinite(usd) || usd <= 0) return 'free'
  if (usd < 0.001) return 'under $0.001'
  return `$${usd < 1 ? usd.toPrecision(2) : usd.toFixed(2)}`
}

/** The estimate before a try: "~$0.0012", "under $0.001" or "free". */
export function estimateText(usd: number): string {
  const s = formatUsd(usd)
  return s.startsWith('$') ? `~${s}` : s
}

export function tryLabel(offer: Pick<StrongerOffer, 'engineLabel' | 'estimateUsd'>): string {
  return `Try with ${offer.engineLabel} (${estimateText(offer.estimateUsd)})`
}

/** Known reasons as plain labels, joined; an unknown key is left out, never shown raw. */
export function reasonCaption(reasons: string[], labels: Record<string, string>): string {
  return reasons.map((r) => labels[r]).filter((l): l is string => !!l).join(' · ')
}

/** Offers by line id; empty when the stronger engine is the drama's own. */
export function buildOffers(s: StrongerEngineSuggestions | null): Map<number, StrongerOffer> {
  const m = new Map<number, StrongerOffer>()
  if (!s || !s.available) return m
  const engineLabel = humanize('engine', s.engine)
  for (const l of s.lines) {
    m.set(l.line_id, {
      engine: s.engine,
      engineLabel,
      caption: reasonCaption(l.reasons, s.reason_labels),
      estimateUsd: l.estimate_usd,
    })
  }
  return m
}

/** The line's English changed after the try: its result must not be applied. */
export function resultIsStale(currentEn: string | null | undefined, basedOnEn: string): boolean {
  return (currentEn ?? '') !== basedOnEn
}

export const STALE_MESSAGE = 'This line changed after the try. Try again to translate the new version.'

/**
 * Plain text for the refusals this route has its own words for, else null
 * (ErrorBanner handles the rest). A 400 is the server's own sentence (the
 * monthly cap); anything path- or key-like is dropped by safeDetail.
 */
export function tryErrorText(err: unknown, engineLabel: string): string | null {
  if (!(err instanceof ApiError)) return null
  switch (err.status) {
    case 400:
      return safeDetail(err.message) ?? `${engineLabel} could not translate this line.`
    case 403:
      return `Your account isn't allowed to use paid engines like ${engineLabel}.`
    case 429:
      return 'Another AI request is running. Try again when it finishes.'
    case 503:
      return `${engineLabel} isn't set up on this PC. Add its key under Settings, then try again.`
    default:
      return null
  }
}
