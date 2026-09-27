import { useEffect, useState } from 'react'

import { api, ApiError } from '../api/client'
import type { DramaSummary } from '../api/types'

// Same choices the Streamlit Library tab offers.
const STATUSES = ['', 'not started', 'aligned', 'translated', 'dubbed', 'exported']
const QUICK_FILTERS = ['', 'Favorite', 'On Hold', 'Plan to Translate']

interface Props {
  selectedId: number | null
  onSelect: (id: number) => void
}

export function LibraryList({ selectedId, onSelect }: Props) {
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')
  const [quickFilter, setQuickFilter] = useState('')
  const [items, setItems] = useState<DramaSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    // Debounced so typing in the search box doesn't send a request per key.
    const timer = setTimeout(() => {
      api
        .listDramas({ search, status, quick_filter: quickFilter })
        .then((resp) => {
          if (!cancelled) {
            setItems(resp.items)
            setError(null)
          }
        })
        .catch((e: unknown) => {
          if (!cancelled) setError(e instanceof ApiError ? e.message : 'Unexpected error.')
        })
    }, 200)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [search, status, quickFilter])

  return (
    <section className="panel" aria-labelledby="library-heading">
      <h2 id="library-heading">Library</h2>
      <div className="filters">
        <input
          type="search"
          placeholder="Search title/summary"
          aria-label="Search title or summary"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s || 'Any status'}
            </option>
          ))}
        </select>
        <select
          aria-label="Quick filter"
          value={quickFilter}
          onChange={(e) => setQuickFilter(e.target.value)}
        >
          {QUICK_FILTERS.map((q) => (
            <option key={q} value={q}>
              {q || 'No quick filter'}
            </option>
          ))}
        </select>
      </div>

      {error && <p className="error" role="alert">{error}</p>}
      {!error && items === null && <p className="muted">Loading…</p>}
      {!error && items !== null && (
        <>
          <p className="muted" data-testid="drama-count">
            {items.length} drama(s)
          </p>
          {items.length > 0 && (
            <table>
              <thead>
                <tr>
                  <th>Title</th>
                  <th>Type</th>
                  <th>Lang</th>
                  <th>Status</th>
                  <th>Tags</th>
                </tr>
              </thead>
              <tbody>
                {items.map((d) => (
                  <tr
                    key={d.id}
                    className={d.id === selectedId ? 'selected' : undefined}
                    onClick={() => onSelect(d.id)}
                  >
                    <td>
                      <button type="button" className="link" onClick={() => onSelect(d.id)}>
                        {d.title_en || d.title_zh || `#${d.id}`}
                      </button>
                      {d.title_en && d.title_zh && <div className="muted">{d.title_zh}</div>}
                    </td>
                    <td>{d.media_type?.replace(/_/g, ' ')}</td>
                    <td>{d.source_language}</td>
                    <td>{d.status}</td>
                    <td>{d.custom_tags.join(', ')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </section>
  )
}
