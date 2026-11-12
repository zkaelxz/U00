// The Terms table's search box and sort choice, with how many terms match.
import { TERM_SORTS, type TermSort } from './glossaryTermView'

export function GlossaryTermsToolbar({ query, onQuery, sort, onSort, shown, total }: {
  query: string
  onQuery: (q: string) => void
  sort: TermSort
  onSort: (s: TermSort) => void
  shown: number
  total: number
}) {
  return (
    <div className="glossary-toolbar actions">
      <input type="search" aria-label="Search terms" placeholder="Search terms" value={query}
        onChange={(e) => onQuery(e.target.value)} />
      <select aria-label="Sort terms" value={sort} onChange={(e) => onSort(e.target.value as TermSort)}>
        {TERM_SORTS.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
      </select>
      <span className="muted" role="status">{shown === total ? `${total} terms` : `${shown} of ${total} terms`}</span>
    </div>
  )
}
