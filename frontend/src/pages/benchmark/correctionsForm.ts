/*
 * Pure helpers for "Add a title's corrections to a test set" (CorrectionsSection.tsx): the reasons it
 * can't be sent yet and the plain-words outcome. No React, so they are tested without a browser
 * (correctionsForm.test.ts).
 */
import type { AddCorrectionsResult, BenchmarkSet, TitleCorrections } from '../../types/benchmark'
import { plural } from '../../labels'

export const MAX_SET_NAME = 60
/** The select value that means "type a new name". */
export const NEW_SET = '__new__'

/** Names of the sets that already hold transcription cases, for the "choose a set" list. */
export function transcriptionSetNames(sets: BenchmarkSet[]): string[] {
  const names = new Set<string>()
  for (const s of sets) if (s.stage === 'transcription' && s.set_name) names.add(s.set_name)
  return [...names].sort((a, b) => a.localeCompare(b))
}

/** The set name the form will send: the chosen one, or the typed new one. */
export const chosenSetName = (choice: string, typed: string) => (choice === NEW_SET ? typed.trim() : choice)

/** Reasons the form can't be sent (empty = it can). */
export function correctionProblems(
  dramaId: number | null, setName: string, stats: TitleCorrections | null, includeAll = false,
): string[] {
  const out: string[] = []
  // Including all lines needs no original transcription and no corrections: the lines as they read are the references.
  if (dramaId == null) out.push('Pick a title.')
  else if (!includeAll && stats && !stats.raw_available) out.push('This title has no original transcription to compare with.')
  else if (!includeAll && stats && stats.lines_corrected === 0) out.push('No corrected lines yet. Edit a line\'s text in Review first.')
  if (!setName) out.push('Choose a set or give a new one a name.')
  return out
}

/** How far the raw transcript was from the corrections, in plain words. */
export function describeStats(s: TitleCorrections): string {
  if (!s.raw_available) return 'No original transcription is kept for this title.'
  const base = `${s.lines_corrected} of ${plural(s.lines_compared, 'line')} corrected`
  if (s.raw_to_edited_cer == null) return `${base}.`
  return `${base}. The raw transcript was ${(s.raw_to_edited_cer * 100).toFixed(1)}% off your text.`
}

/** What a preview or an add did, in plain words. */
export function describeAdd(r: AddCorrectionsResult): string {
  const all = r.include_all ? ' Current text counts as the reference.' : ''
  const left = r.unedited_skipped ? ` ${plural(r.unedited_skipped, 'unedited line')} skipped.` : ''
  const long = r.unclippable_skipped ? ` ${plural(r.unclippable_skipped, 'line')} too short or long for a clip skipped.` : ''
  const more = r.remaining ? ` ${plural(r.remaining, 'line')} left: add again for the rest (at most 100 per add).` : ''
  if (r.dry_run) return `This would add ${plural(r.case_count, 'clip')} to “${r.set_name}”.${all}${more}${left}${long}`
  const had = r.skipped ? ` ${plural(r.skipped, 'line')} already in the set ${r.skipped === 1 ? 'was' : 'were'} skipped.` : ''
  return `Added ${plural(r.added, 'clip')} to “${r.set_name}”.${all}${had}${more}${left}${long}`
}
