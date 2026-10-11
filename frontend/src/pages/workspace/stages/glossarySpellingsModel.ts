// Pure helpers for the "Suggest spellings" and "Check English names" panel.
import type { NameConsistency, SpellingSuggestStatus } from '../../../types/translateStage'

/** What to say when a finished run has nothing to list, or null when there are proposals to show. */
export function spellingsEmptyText(status: SpellingSuggestStatus | null): string | null {
  const r = status?.result
  if (status?.status !== 'done' || !r) return null
  if (r.unusable) return "The model's answer could not be used. Nothing was changed; try again or pick another engine."
  if (r.proposals.length === 0) return "None of the model's guesses appear in this title's lines."
  return null
}

/** A finished or running run counts for this term only; another term's result is not shown here. */
export function statusForTerm(status: SpellingSuggestStatus | null, termOriginal: string): SpellingSuggestStatus | null {
  if (!status) return null
  if (status.status === 'done' && status.result?.term !== termOriginal) return null
  return status
}

/** One plain sentence summarising the consistency check. */
export function consistencySummary(c: NameConsistency): string {
  if (!c.available) return c.reason
  if (c.lines_with_name === 0) return `No line in the source mentions “${c.term}” or its aliases.`
  const gap = c.untranslated ? ` ${c.untranslated} not translated yet.` : ''
  if (c.differing.length === 0) return `Every translated line that names it uses “${c.english}”.${gap}`
  return `${c.differing.length} of ${c.lines_with_name} lines do not use “${c.english}”.${gap}`
}
