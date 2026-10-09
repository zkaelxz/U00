// Pure helpers for the Auto-tune and Glossary-from-novel panels (unit-tested
// in autotuneGlossary.test.ts).

import type {
  NovelGlossaryApplyResult,
  NovelGlossaryProposal,
} from '../../../types/autotuneGlossary'

// "Select all High": the High-confidence proposals not already in the glossary.
export function highConfidenceTerms(proposals: NovelGlossaryProposal[]): Set<string> {
  return new Set(proposals.filter((p) => p.confidence === 'high' && !p.already_in_glossary).map((p) => p.term))
}

export const isActiveStatus = (status: string | null | undefined) =>
  status === 'queued' || status === 'running'

const pct = (progress: number | null) =>
  progress === null ? '' : ` ${Math.round(Math.min(Math.max(progress, 0), 1) * 100)}%`

// --- Auto-tune ----------------------------------------------------------

// The job reports "Testing candidate 2 of 3 (800ms)..."; shown as
// "Testing 2 of 3 (800 ms)…".
export function autotuneProgressText(status: string, message: string): string {
  if (status === 'queued') return 'Waiting for the GPU…'
  const m = /(\d+)\s+of\s+(\d+)\s*\((\d+)\s*ms\)/i.exec(message)
  return m ? `Testing ${m[1]} of ${m[2]} (${m[3]} ms)…` : 'Testing…'
}

// "2:05" (or "1:02:05"): whole seconds as a clock.
export function formatElapsed(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const ss = String(s % 60).padStart(2, '0')
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`
}

// Time left, from how long the finished candidates took: null until one has
// finished (the job reports "candidate N of M" as each starts, so N-1 are done).
export function autotuneEta(elapsedSeconds: number, message: string): string | null {
  const m = /(\d+)\s+of\s+(\d+)/i.exec(message)
  if (!m) return null
  const n = Number(m[1])
  const total = Number(m[2])
  if (n < 2 || total < n) return null
  const left = ((total - n + 1) * elapsedSeconds) / (n - 1)
  return `about ${formatElapsed(Math.round(left))} left`
}

export function autotuneBlocker(hasAudio: boolean, busy: boolean): string | null {
  if (busy) return 'Wait for the running job to finish.'
  if (!hasAudio) return 'Still needed: audio on this drama.'
  return null
}

export const AUTOTUNE_EXPIRED = 'Run auto-tune again (results are kept only until the app restarts).'

// Apply answers 400 (no finished run held) or 422 (value not measured)
// once the app has restarted or the run was replaced.
export function autotuneApplyErrorText(err: unknown): string | null {
  const e = err as { status?: number } | null
  return e && (e.status === 400 || e.status === 422) ? AUTOTUNE_EXPIRED : null
}

// --- Glossary from novel -----------------------------------------------

export interface Blocker {
  text: string
  link: string
  href: string
}

export function novelGlossaryBlocker(
  dramaId: number,
  seriesId: number | null,
  hasNovel: boolean,
): Blocker | null {
  const href = `#/drama/${dramaId}/source`
  if (!seriesId) return { text: 'Still needed: a series for this drama', link: 'set it in Details', href }
  if (!hasNovel) return { text: 'Still needed: novel text', link: 'attach it on Source', href }
  return null
}

export function novelGlossaryProgressText(status: string, progress: number | null): string {
  return status === 'queued' ? 'Waiting to start…' : `Reading the novel…${pct(progress)}`
}

export const PAID_ENGINE_TEXT = "This engine is paid and this account can't use it."
export const ENGINE_CHANGED_TEXT = "The drama's engine changed; start again."

export function novelGlossaryStartErrorText(err: unknown): string | null {
  const e = err as { status?: number } | null
  if (e?.status === 403) return PAID_ENGINE_TEXT
  if (e?.status === 409) return ENGINE_CHANGED_TEXT
  return null
}

// New terms start checked; ones already in the glossary start unchecked
// (they only matter with "Overwrite existing"). Keyed by term text, never
// by position.
export function defaultTermSelection(proposals: NovelGlossaryProposal[]): Set<string> {
  return new Set(proposals.filter((p) => !p.already_in_glossary).map((p) => p.term))
}

export function toggleTerm(sel: Set<string>, term: string): Set<string> {
  const next = new Set(sel)
  if (next.has(term)) next.delete(term)
  else next.add(term)
  return next
}

// Selected terms in proposal order (only ones still proposed).
export function chosenTerms(proposals: NovelGlossaryProposal[], sel: Set<string>): string[] {
  return proposals.filter((p) => sel.has(p.term)).map((p) => p.term)
}

// How many chosen terms are in the series glossary right now (re-read at
// confirm time, not the run's already_in_glossary snapshot).
export function countInGlossary(chosen: string[], glossaryOriginals: string[]): number {
  const have = new Set(glossaryOriginals.map((t) => t.trim()))
  return chosen.filter((t) => have.has(t.trim())).length
}

// null = the current glossary could not be read; say "may" instead of a count.
export function overwriteConfirmText(existing: number | null): string {
  if (existing === null) return 'This may replace existing terms in the series glossary.'
  if (existing === 0) return 'None of the chosen terms are in the series glossary now; they will be added.'
  return `Replace ${existing} existing term${existing === 1 ? '' : 's'} in the series glossary?`
}

export const GLOSSARY_EXPIRED = 'Run the extraction again (results are kept only until the app restarts).'

// Apply answers 400 when no finished extraction is held (app restarted).
export function novelGlossaryApplyErrorText(err: unknown): string | null {
  const e = err as { status?: number } | null
  return e?.status === 400 ? GLOSSARY_EXPIRED : null
}

export const addTermsLabel = (n: number) => `Add ${n} term${n === 1 ? '' : 's'} to series glossary`

export function applySummary(r: NovelGlossaryApplyResult): string {
  const parts: string[] = []
  parts.push(`Added ${r.added.length}.`)
  if (r.overwritten.length) parts.push(`Overwrote ${r.overwritten.length}.`)
  if (r.skipped_existing.length) parts.push(`Skipped ${r.skipped_existing.length} already in the glossary.`)
  if (r.unknown.length) parts.push(`${r.unknown.length} no longer proposed.`)
  return parts.join(' ')
}

// A held scan worth reopening on return: running, failed, or finished with
// proposals not yet applied. Finished-empty and cancelled need no review.
export const isResumableScan = (s: { status: string; proposals?: unknown[] | null }): boolean =>
  isActiveStatus(s.status) || s.status === 'error' || (s.status === 'done' && (s.proposals?.length ?? 0) > 0)
