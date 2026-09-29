// Pure helpers for the Review "AI extras" section (AiExtras*.tsx).
import { idxFromLineNumber, lineNumber } from '../../../../lineNumber'
import type {
  BurnPreviewClip,
  MergeShortOptions,
  MergeShortPreview,
  SenseVoiceTags,
  StyleState,
} from '../../../../types/reviewExtras'

// Mirrors services/review_extras_service.py MERGE_DEFAULTS / MERGE_LIMITS.
export const MERGE_DEFAULTS: MergeShortOptions = { min_duration: 1.2, max_gap: 0.5, max_chars: 80 }
export const MERGE_LIMITS: Record<keyof MergeShortOptions, [number, number]> = {
  min_duration: [0.1, 10],
  max_gap: [0, 5],
  max_chars: [10, 500],
}

export type MergeForm = Record<keyof MergeShortOptions, string>

export const mergeFormDefaults = (): MergeForm => ({
  min_duration: String(MERGE_DEFAULTS.min_duration),
  max_gap: String(MERGE_DEFAULTS.max_gap),
  max_chars: String(MERGE_DEFAULTS.max_chars),
})

const LABELS: Record<keyof MergeShortOptions, string> = {
  min_duration: 'Short line',
  max_gap: 'Max gap',
  max_chars: 'Max length',
}

/** Form text to options, or per-field errors (nothing is sent while any remain). */
export function parseMergeForm(form: MergeForm): {
  options: MergeShortOptions | null
  errors: Partial<Record<keyof MergeShortOptions, string>>
} {
  const errors: Partial<Record<keyof MergeShortOptions, string>> = {}
  const out: Partial<MergeShortOptions> = {}
  for (const key of Object.keys(MERGE_LIMITS) as (keyof MergeShortOptions)[]) {
    const raw = form[key].trim()
    const n = raw === '' ? Number.NaN : Number(raw)
    const [lo, hi] = MERGE_LIMITS[key]
    if (!Number.isFinite(n) || n < lo || n > hi) errors[key] = `${LABELS[key]}: ${lo}–${hi}`
    else if (key === 'max_chars' && !Number.isInteger(n)) errors[key] = `${LABELS[key]}: a whole number`
    else out[key] = n
  }
  return Object.keys(errors).length ? { options: null, errors } : { options: out as MergeShortOptions, errors }
}

export function mergeSummary(p: Pick<MergeShortPreview, 'line_count_before' | 'line_count_after' | 'groups'>): string {
  if (p.groups.length === 0) return `No short lines to merge (${p.line_count_before} lines).`
  const n = p.groups.length
  return `${p.line_count_before} → ${p.line_count_after} lines: ${n} merge${n === 1 ? '' : 's'}.`
}

export function styleSummary(s: StyleState): string {
  if (!s.profile) {
    return s.edit_count >= s.min_samples
      ? `${s.edit_count} edits recorded. Nothing learned yet.`
      : `${s.edit_count} of ${s.min_samples} edits needed to learn a style.`
  }
  const conf = s.profile.confidence ? ` · ${s.profile.confidence} confidence` : ''
  const state = s.profile.applied ? 'on' : 'paused'
  return `${s.profile.preferences.length} preferences · from ${s.profile.sample_count} edits${conf} · ${state}`
}

export function senseVoiceSummary(t: Pick<SenseVoiceTags, 'tagged' | 'disagree'>): string {
  if (t.tagged === 0) return 'Not tagged yet.'
  return `${t.tagged} line${t.tagged === 1 ? '' : 's'} tagged from the audio · ${t.disagree} disagree with the text-based read`
}

/**
 * A typed line number ("12" or "#12") to that line's permanent id, using the
 * drama's lines as loaded. Never sends the number itself to the API.
 */
export function resolveLineNumber(
  lines: { id: number; idx: number }[],
  input: string,
): { lineId: number } | { error: string } {
  const m = /^#?\s*(\d{1,6})$/.exec(input.trim())
  if (!m) return { error: 'Enter a line number, e.g. 12.' }
  const idx = idxFromLineNumber(Number(m[1]))
  const line = lines.find((l) => l.idx === idx)
  return line ? { lineId: line.id } : { error: `No line #${m[1]} in this drama.` }
}

export function fmtSeconds(s: number | null | undefined): string {
  if (s === null || s === undefined || !Number.isFinite(s)) return '–'
  const m = Math.floor(s / 60)
  const sec = s - m * 60
  return `${m}:${sec.toFixed(1).padStart(4, '0')}`
}

export function clipCaption(c: BurnPreviewClip): string {
  const line = c.idx !== null && c.idx !== undefined ? `Line #${lineNumber(c.idx)}` : 'Preview'
  const range = c.start !== null && c.end !== null ? ` · ${fmtSeconds(c.start)}–${fmtSeconds(c.end)}` : ''
  return `${line}${range}${c.preset ? ` · ${c.preset}` : ''}`
}
