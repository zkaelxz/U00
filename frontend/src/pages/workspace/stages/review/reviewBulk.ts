// Parity R49: the half-price Bulk option for the Review stage's AI checks
// (consistency, emotion, notes, flag). The server runs them through
// Claude's or Gemini's batch API and refuses bulk for any other engine, or
// for Gemini on its free tier (review_jobs_service._start).
import type { ReviewJobBody, ReviewJobKind } from '../../../../types/review'
import { checkJobBody, type CheckForm } from './reviewResults'

export type BulkKind = Exclude<ReviewJobKind, 'fix-flagged'>
export type BulkChoice = Partial<Record<BulkKind, boolean>>

export const BULK_HELP =
  "Half price through Claude's or Gemini's batch service, but slow: most results arrive within an hour, some take up to 24 hours. " +
  'They are applied to each line by its id when they arrive. Until then, adding, deleting, merging, splitting or re-segmenting lines, ' +
  'restoring a version and deleting the drama are refused.'

/** The engine the job runs on: the one picked, else the drama's. */
export function effectiveEngine(f: Pick<CheckForm, 'engine'>, defaultEngine: string): string {
  return f.engine.trim() || defaultEngine
}

/**
 * Bulk review needs Claude, or Gemini with the free tier off.
 * `supported` is the config's bulk_supported_engines, which already drops
 * Gemini while the free tier is on (it also lists DeepSeek, which only
 * bulk-translates).
 */
export function reviewBulkAvailable(engine: string, supported: readonly string[]): boolean {
  return (engine === 'claude' || engine === 'gemini') && supported.includes(engine)
}

/** Why Bulk can't be used with this engine, or null when it can. */
export function bulkBlocker(engine: string, supported: readonly string[]): string | null {
  if (reviewBulkAvailable(engine, supported)) return null
  if (engine === 'gemini') return "Still needed for Bulk: Gemini's free tier turned off in Settings, or Claude as the engine."
  return 'Still needed for Bulk: Claude or Gemini as the engine (Check options).'
}

/** The start body; bulk only when it is on and the engine allows it. */
export function reviewStartBody(
  kind: BulkKind,
  f: CheckForm,
  choice: BulkChoice,
  defaultEngine: string,
  supported: readonly string[],
): ReviewJobBody {
  const bulk = !!choice[kind] && reviewBulkAvailable(effectiveEngine(f, defaultEngine), supported)
  return checkJobBody(kind, f, bulk)
}

/** Shown once a bulk batch is accepted. */
export function bulkStartedText(label: string, lineCount: number): string {
  const lines = `${lineCount} line${lineCount === 1 ? '' : 's'}`
  return (
    `${label}: sent as a bulk batch at half price (${lines}). Results arrive later, usually within an hour and at most 24 hours. ` +
    'Until they are applied or the batch is cancelled, structural edits (add, delete, merge, split, re-segment, restore a version, delete the drama) are paused.'
  )
}
