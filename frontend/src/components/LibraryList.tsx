import { useEffect, useRef, useState } from 'react'

import { api, ApiError } from '../api/client'
import type { DramaSummary } from '../api/types'
import { useMediaQuery } from '../hooks/useMediaQuery'
import { MAX_SELECTION, selectAllVisible, toggleId } from '../pages/libraryAdmin/libraryAdmin'
import { DramaCards } from './DramaCards'

// Same choices the Streamlit Library tab offers.
const STATUSES = ['', 'not started', 'aligned', 'translated', 'dubbed', 'exported']
const QUICK_FILTERS = ['', 'Favorite', 'On Hold', 'Plan to Translate']

interface Props {
  selectedId: number | null
  onSelect: (id: number) => void
  // Bump to refetch after a create/delete elsewhere on the page.
  reloadKey?: number
  // Library admin selection (omit for a plain list). Desktop: a checkbox
  // column; phone: a Select toggle that turns cards into checkboxes.
  checked?: ReadonlySet<number>
  onCheckedChange?: (next: Set<number>) => void
  selectMode?: boolean
  onSelectModeChange?: (on: boolean) => void
  // Every successful load (for pruning the selection and the admin bar).
  onItems?: (items: DramaSummary[]) => void
}

export function LibraryList({
  selectedId, onSelect, reloadKey = 0, checked, onCheckedChange, selectMode, onSelectModeChange, onItems,
}: Props) {
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')
  const [quickFilter, setQuickFilter] = useState('')
  const [items, setItems] = useState<DramaSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const phone = useMediaQuery('(max-width: 640px)')
  const onItemsRef = useRef(onItems)
  useEffect(() => {
    onItemsRef.current = onItems
  })
  const selecting = !!checked && !!onCheckedChange
  const toggle = (id: number) => checked && onCheckedChange?.(toggleId(checked, id))
  const allChecked = !!items?.length && !!checked && items.slice(0, MAX_SELECTION).every((d) => checked.has(d.id))

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
            onItemsRef.current?.(resp.items)
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
  }, [search, status, quickFilter, reloadKey])

  return (
    <section className="panel wide" aria-labelledby="library-heading">
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
          <div className="list-head">
            <p className="muted" data-testid="drama-count">
              {items.length} drama(s)
            </p>
            {selecting && phone && items.length > 0 && onSelectModeChange && !selectMode && (
              <button type="button" onClick={() => onSelectModeChange(true)}>
                Select
              </button>
            )}
          </div>
          {selecting && items.length > MAX_SELECTION && (
            <p className="muted">At most {MAX_SELECTION} at a time.</p>
          )}
          {items.length > 0 && phone && (
            <DramaCards
              items={items}
              selectedId={selectedId}
              onSelect={onSelect}
              selectMode={selecting && selectMode}
              checked={checked}
              onToggle={toggle}
            />
          )}
          {items.length > 0 && !phone && (
            <table>
              <thead>
                <tr>
                  {selecting && (
                    <th className="check-col">
                      <input
                        type="checkbox"
                        aria-label="Select all visible"
                        checked={allChecked}
                        onChange={() => onCheckedChange?.(allChecked ? new Set() : selectAllVisible(items).ids)}
                      />
                    </th>
                  )}
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
                    {selecting && (
                      <td className="check-col" onClick={(e) => e.stopPropagation()}>
                        <input
                          type="checkbox"
                          aria-label={`Select ${d.title_en || d.title_zh || `#${d.id}`}`}
                          checked={!!checked?.has(d.id)}
                          onChange={() => toggle(d.id)}
                        />
                      </td>
                    )}
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
