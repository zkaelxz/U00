import type { GlossaryTerm, GlossaryTermUpsert, NameMark, NameReport, NameReportGroup } from '../../../types/translateStage'
import { aliasesWith } from './glossaryVariantsModel'

export type NameTab = 'open' | NameMark

export const NAME_TABS: { id: NameTab; label: string }[] = [
  { id: 'open', label: 'To check' },
  { id: 'right', label: 'Right' },
  { id: 'wrong', label: 'Wrong' },
  { id: 'skip', label: 'Skipped' },
]

export const MARK_HINTS: Record<NameMark, string> = {
  right: 'These spellings are one name. It stays out of the To check list; you can then add it to glossaries.',
  wrong: 'These are different names. It will not come back in later checks.',
  skip: 'Not now. It will not come back in later checks; find it under Skipped.',
}

export function groupsForTab(report: NameReport, tab: NameTab): NameReportGroup[] {
  return report.groups.filter((g) => (tab === 'open' ? g.mark === null : g.mark === tab))
}

export function tabCount(report: NameReport, tab: NameTab): number {
  return tab === 'open' ? report.counts.unresolved : report.counts[tab]
}

/** Plain-language notes on what the check did not cover (a cap, a missing reading library, a failed title). */
export function scanNotes(r: NameReport): string[] {
  const notes: string[] = []
  if (r.titles_scanned < r.titles_total) {
    notes.push(`Only ${r.titles_scanned} of ${r.titles_total} titles were checked (the newest first). Run it again after clearing some marks or use the per-title check for the rest.`)
  }
  if (r.titles_unavailable) {
    notes.push(`${r.titles_unavailable} title${r.titles_unavailable === 1 ? ' was' : 's were'} skipped: ${r.reasons.join(' ') || 'a reading library is missing.'}`)
  }
  if (r.titles_failed) notes.push(`${r.titles_failed} title${r.titles_failed === 1 ? '' : 's'} could not be read and ${r.titles_failed === 1 ? 'was' : 'were'} skipped.`)
  if (r.groups_omitted) notes.push(`${r.groups_omitted} more group${r.groups_omitted === 1 ? ' was' : 's were'} left out to keep the list short.`)
  return notes
}

const norm = (s: string) => s.normalize('NFKC').trim().toLowerCase()

export type ApplyPlan =
  | { kind: 'create' | 'alias'; body: GlossaryTermUpsert }
  | { kind: 'covered' }
  | { kind: 'needs-english' }

/**
 * What to send to the existing term route for one title, from that title's current glossary:
 * add the group's missing spellings as aliases of the term that already holds any of them, else
 * create a term (the canonical spelling with the others as aliases) when an English name is given.
 */
export function planApply(terms: readonly GlossaryTerm[], group: NameReportGroup, english: string): ApplyPlan {
  const spellings = group.variants.map((v) => v.text)
  const wanted = new Set(spellings.map(norm))
  const held = terms.find((t) => wanted.has(norm(t.term_original)) || t.aliases.some((a) => wanted.has(norm(a))))
  if (held) {
    const have = new Set([held.term_original, ...held.aliases].map(norm))
    const missing = spellings.filter((s) => !have.has(norm(s)))
    if (!missing.length) return { kind: 'covered' }
    return { kind: 'alias', body: { id: held.id, aliases: missing.reduce(aliasesWith, held.aliases) } }
  }
  const name = english.trim()
  if (!name) return { kind: 'needs-english' }
  const others = spellings.filter((s) => norm(s) !== norm(group.canonical))
  return {
    kind: 'create',
    body: { term_original: group.canonical, term_translation: name, aliases: others.reduce(aliasesWith, [] as string[]) },
  }
}
