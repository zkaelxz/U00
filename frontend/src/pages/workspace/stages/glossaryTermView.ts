// What the Terms table shows: the search box and sort order applied to the terms the server sent.
import type { GlossaryTerm } from '../../../types/translateStage'

export type TermSort = 'listed' | 'newest' | 'original' | 'translation'

export const TERM_SORTS: { id: TermSort; label: string }[] = [
  { id: 'listed', label: 'As saved' },
  { id: 'newest', label: 'Newest first' },
  { id: 'original', label: 'Original A to Z' },
  { id: 'translation', label: 'Translation A to Z' },
]

export function filterTerms(terms: GlossaryTerm[], query: string): GlossaryTerm[] {
  const q = query.trim().toLowerCase()
  if (!q) return terms
  return terms.filter((t) =>
    [t.term_original, t.term_translation, ...(t.aliases ?? [])].some((s) => (s ?? '').toLowerCase().includes(q)))
}

export function sortTerms(terms: GlossaryTerm[], sort: TermSort): GlossaryTerm[] {
  if (sort === 'listed') return terms
  if (sort === 'newest') return [...terms].sort((a, b) => b.id - a.id)
  const key = sort === 'original' ? 'term_original' : 'term_translation'
  return [...terms].sort((a, b) => (a[key] ?? '').localeCompare(b[key] ?? ''))
}
