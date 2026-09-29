import { useEffect, useRef, useState } from 'react'

import { listLines, searchLines } from '../../../../api/review'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import type { LineFilter, ReviewLine, ReviewLinesPage } from '../../../../types/review'
import { LineRow } from './LineRow'
import { PAGE_SIZE, pageCount } from './reviewLogic'

interface Props {
  dramaId: number
  reloads: number
  onChanged: () => void
}

export function LinesPanel({ dramaId, reloads, onChanged }: Props) {
  const [filter, setFilter] = useState<LineFilter>('all')
  const [page, setPage] = useState(1)
  const [input, setInput] = useState('')
  const [term, setTerm] = useState('')
  const [data, setData] = useState<ReviewLinesPage | null>(null)
  const [found, setFound] = useState<ReviewLine[] | null>(null)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    let cancelled = false
    const req = term
      ? searchLines(dramaId, term).then((r) => !cancelled && setFound(r))
      : listLines(dramaId, page, PAGE_SIZE, filter).then(
          (r) => !cancelled && (setFound(null), setData(r)),
        )
    req.then(
      () => !cancelled && setError(null),
      (e: unknown) => !cancelled && setError(e),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, page, filter, term, reloads])

  const listRef = useRef<HTMLUListElement>(null)

  // Alt+Down / Alt+Up move focus to the next / previous flagged line on this page.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!e.altKey || e.ctrlKey || e.metaKey || e.isComposing) return
      if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
      const rows = Array.from(listRef.current?.querySelectorAll<HTMLElement>('li[data-flagged="true"]') ?? [])
      if (rows.length === 0) return
      e.preventDefault()
      const current = rows.findIndex((r) => r.contains(document.activeElement))
      const down = e.key === 'ArrowDown'
      const next = current === -1 ? (down ? 0 : rows.length - 1) : Math.min(rows.length - 1, Math.max(0, current + (down ? 1 : -1)))
      rows[next].focus()
      rows[next].scrollIntoView?.({ block: 'nearest' })
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  const shown = found ?? data?.lines ?? []
  const pages = data ? pageCount(data.total) : 1

  return (
    <section className="panel" aria-label="Lines">
      <div className="review-head">
        <h3>Lines</h3>
        <button
          type="button"
          className="review-hint"
          aria-label="Keyboard shortcuts"
          title="Click a translation to edit it. Enter or Ctrl+S saves, Esc cancels. Alt+Down / Alt+Up jump to the next / previous flagged line."
        >
          ?
        </button>
        {data && (
          <p className="muted" data-testid="line-counts">
            {data.total} in this view · {data.flagged_count} flagged · {data.untranslated_count} untranslated
          </p>
        )}
      </div>
      <form
        className="filters"
        onSubmit={(e) => {
          e.preventDefault()
          setTerm(input.trim())
        }}
      >
        <select
          aria-label="Show"
          value={filter}
          onChange={(e) => { setFilter(e.target.value as LineFilter); setPage(1); setInput(''); setTerm('') }}
        >
          <option value="all">All lines</option>
          <option value="flagged">Flagged only</option>
          <option value="untranslated">Untranslated only</option>
        </select>
        <input
          aria-label="Search lines"
          placeholder="Search source or translation"
          value={input}
          onChange={(e) => setInput(e.target.value)}
        />
        <button type="submit">Search</button>
        {term && (
          <button type="button" onClick={() => { setInput(''); setTerm('') }}>
            Clear search
          </button>
        )}
      </form>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {shown.length === 0 && !error && <p className="muted">No lines to show.</p>}
      <ul className="review-lines" ref={listRef}>
        {shown.map((l) => (
          <LineRow key={l.id} dramaId={dramaId} line={l} onChanged={onChanged} />
        ))}
      </ul>
      {!term && data && pages > 1 && (
        <div className="review-pager">
          <button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
          <span data-testid="page-label">Page {page} of {pages}</span>
          <button type="button" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button>
        </div>
      )}
    </section>
  )
}
