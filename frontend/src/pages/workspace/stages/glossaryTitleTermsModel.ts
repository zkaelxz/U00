import type { GlossaryCopyTitleTermsResult, GlossaryTitleTerm } from '../../../types/translateStage'

/** What the title's own terms mean while it sits in a series, in two plain sentences. */
export function titleTermsSummary(terms: GlossaryTitleTerm[]): string {
  const shadowed = terms.filter((t) => t.clashes_with_series).length
  const own = `This title has ${terms.length} term${terms.length === 1 ? '' : 's'} of its own. They are used in translations and win over the series glossary.`
  if (!shadowed) return own
  return `${own} ${shadowed} of them ${shadowed === 1 ? 'has' : 'have'} the same original text as a series term, so ${shadowed === 1 ? 'it' : 'they'} can't be edited or removed here; the series list above shows only the series' version.`
}

/** The answer to "Copy to series": what was added and what the series already had. */
export function copyResultText(r: GlossaryCopyTitleTermsResult): string {
  const added = `${r.copied.length} term${r.copied.length === 1 ? '' : 's'} copied to the series.`
  return r.skipped.length ? `${added} ${r.skipped.length} already in the series, left as they are.` : added
}
