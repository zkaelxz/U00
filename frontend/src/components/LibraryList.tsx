import { useEffect, useRef, useState, type FormEvent } from 'react'

import { api, ApiError } from '../api/client'
import { searchLines } from '../api/library'
import type { DramaSummary } from '../api/types'
import { usePersistedState } from '../hooks/usePersistedState'
import { useMediaQuery } from '../hooks/useMediaQuery'
import { languageLabel, mediaTypeLabel, statusLabel } from '../labels'
import { lineNumber } from '../lineNumber'
import { MAX_SELECTION, selectAllVisible, toggleId } from '../pages/libraryAdmin/libraryAdmin'
import type { LibrarySearchHit } from '../types/library'
import { Badge } from './Badge'
import { Card } from './Card'
import { DramaCards } from './DramaCards'
import { ErrorBanner } from './ErrorBanner'
import { countDramas, dramaName, readHref, workspaceHref } from './libraryView'
import { buttonClass } from './uiClasses'

// Same choices the Streamlit Library tab offers.
const STATUSES = ['', 'not started', 'aligned', 'translated', 'dubbed', 'exported']
const QUICK_FILTERS = ['', 'Favorite', 'On Hold', 'Plan to Translate']

type Scope = 'titles' | 'lines'
type View = 'grid' | 'list'

interface Props {
  selectedId: number | null
  onSelect: (id: number) => void
  // Bump to refetch after a create/delete elsewhere on the page.
  reloadKey?: number
  // Library admin selection (omit for a plain list). List view on desktop:
  // a checkbox column; grid (and phone): a Select button that turns cards
  // into checkboxes.
  checked?: ReadonlySet<number>
  onCheckedChange?: (next: Set<number>) => void
  selectMode?: boolean
  onSelectModeChange?: (on: boolean) => void
  // Every successful load (for pruning the selection and the admin bar).
  onItems?: (items: DramaSummary[]) => void
  // "New drama" from the empty-library card.
  onCreate?: () => void
}

// Two to three native radios drawn as one segmented control.
function Segmented<T extends string>({ label, name, value, options, onChange }: {
  label: string; name: string; value: T; options: [T, string][]; onChange: (v: T) => void
}) {
  return (
    <fieldset className="segmented">
      <legend className="visually-hidden">{label}</legend>
      {options.map(([v, text]) => (
        <label key={v}>
          <input type="radio" name={name} value={v} checked={value === v} onChange={() => onChange(v)} />
          <span>{text}</span>
        </label>
      ))}
    </fieldset>
  )
}

export function LibraryList({
  selectedId, onSelect, reloadKey = 0, checked, onCheckedChange, selectMode, onSelectModeChange, onItems, onCreate,
}: Props) {
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')
  const [quickFilter, setQuickFilter] = useState('')
  const [scope, setScope] = useState<Scope>('titles')
  const [view, setView] = usePersistedState<View>('library.view', 'grid')
  const [items, setItems] = useState<DramaSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [hits, setHits] = useState<LibrarySearchHit[] | null>(null)
  const [hitsError, setHitsError] = useState<unknown>(null)
  const phone = useMediaQuery('(max-width: 640px)')
  const onItemsRef = useRef(onItems)
  useEffect(() => {
    onItemsRef.current = onItems
  })
  const selecting = !!checked && !!onCheckedChange
  const toggle = (id: number) => checked && onCheckedChange?.(toggleId(checked, id))
  const allChecked = !!items?.length && !!checked && items.slice(0, MAX_SELECTION).every((d) => checked.has(d.id))
  const toggleAll = () => items && onCheckedChange?.(allChecked ? new Set() : selectAllVisible(items).ids)
  // Phones always get cards; List view is the desktop table.
  const table = view === 'list' && !phone
  const titleSearch = scope === 'titles' ? search : ''
  const filtered = !!(titleSearch.trim() || status || quickFilter)

  useEffect(() => {
    let cancelled = false
    // Debounced so typing in the search box doesn't send a request per key.
    const timer = setTimeout(() => {
      api
        .listDramas({ search: titleSearch, status, quick_filter: quickFilter })
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
  }, [titleSearch, status, quickFilter, reloadKey])

  const submit = (e: FormEvent) => {
    e.preventDefault()
    const term = search.trim()
    if (scope !== 'lines' || !term) return
    searchLines(term).then(
      (r) => { setHits(r.items); setHitsError(null) },
      (err: unknown) => setHitsError(err),
    )
  }

  const clearFilters = () => {
    setSearch('')
    setStatus('')
    setQuickFilter('')
  }

  const selectControls = selecting && items && items.length > 0 && scope === 'titles' && (
    // Desktop List view has its own checkbox column; everywhere else the
    // cards turn into checkboxes in select mode.
    !table && (selectMode ? (
      <>
        <button type="button" className={buttonClass('ghost', 'sm')} onClick={toggleAll}>
          {allChecked ? 'Select none' : 'Select all'}
        </button>
        {!phone && (
          <button type="button" className={buttonClass('ghost', 'sm')} onClick={() => { onSelectModeChange?.(false); onCheckedChange?.(new Set()) }}>
            Done selecting
          </button>
        )}
      </>
    ) : onSelectModeChange && (
      <button type="button" className={buttonClass('ghost', 'sm')} onClick={() => onSelectModeChange(true)}>
        Select
      </button>
    ))
  )

  return (
    <section className="library-list" aria-labelledby="library-heading">
      <h3 id="library-heading" className="visually-hidden">Dramas</h3>
      <form className="library-toolbar" role="search" onSubmit={submit}>
        <div className="toolbar-search">
          <input
            type="search"
            placeholder={scope === 'titles' ? 'Search titles and summaries' : 'Search every line, then press Enter'}
            aria-label={scope === 'titles' ? 'Search title or summary' : 'Search all lines'}
            maxLength={200}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          {scope === 'lines' && <button type="submit" className={buttonClass('secondary')}>Search</button>}
        </div>
        {scope === 'titles' && <div className="toolbar-filters">
          <select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
            {STATUSES.map((s) => (
              <option key={s} value={s}>{s ? statusLabel(s) : 'Any status'}</option>
            ))}
          </select>
          <select aria-label="Quick filter" value={quickFilter} onChange={(e) => setQuickFilter(e.target.value)}>
            {QUICK_FILTERS.map((q) => (
              <option key={q} value={q}>{q || 'All lists'}</option>
            ))}
          </select>
          {/* Slot for the studio / author / voice actor / language / type / tag
              filters (react-misc-parity); they join this row. */}
        </div>}
        <div className="toolbar-row">
          <Segmented<Scope>
            label="Search in" name="library-scope" value={scope} onChange={setScope}
            options={[['titles', 'Titles'], ['lines', 'Lines']]}
          />
          {!phone && scope === 'titles' && (
            <Segmented<View>
              label="View" name="library-view" value={view} onChange={setView}
              options={[['grid', 'Grid'], ['list', 'List']]}
            />
          )}
          <div className="toolbar-end">{selectControls}</div>
        </div>
      </form>

      {scope === 'lines' ? (
        <LineHits hits={hits} error={hitsError} />
      ) : (
        <>
          {error && <p className="error" role="alert">{error}</p>}
          {!error && items === null && <SkeletonGrid />}
          {!error && items !== null && (
            <>
              <p className="muted list-count" data-testid="drama-count">{countDramas(items.length)}</p>
              {selecting && items.length > MAX_SELECTION && (
                <p className="muted">At most {MAX_SELECTION} at a time.</p>
              )}
              {items.length === 0 && !filtered && (
                <Card title="No dramas yet" meta="Add one to start transcribing, translating or reading.">
                  {onCreate && (
                    <div className="actions">
                      <button type="button" className={buttonClass('primary')} onClick={onCreate}>New drama</button>
                    </div>
                  )}
                </Card>
              )}
              {items.length === 0 && filtered && (
                <div className="empty-note">
                  <p>No dramas match.</p>
                  <button type="button" className={buttonClass('ghost', 'sm')} onClick={clearFilters}>Clear filters</button>
                </div>
              )}
              {items.length > 0 && !table && (
                <DramaCards
                  items={items}
                  selectedId={selectedId}
                  onSelect={onSelect}
                  selectMode={selecting && selectMode}
                  checked={checked}
                  onToggle={toggle}
                />
              )}
              {items.length > 0 && table && (
                <div className="table-scroll drama-table">
                  <table>
                    <thead>
                      <tr>
                        {selecting && (
                          <th className="check-col">
                            <label className="check-hit">
                              <input type="checkbox" aria-label="Select all visible" checked={allChecked} onChange={toggleAll} />
                            </label>
                          </th>
                        )}
                        <th>Title</th>
                        <th>Type</th>
                        <th>Language</th>
                        <th>Status</th>
                        <th>Tags</th>
                        <th><span className="visually-hidden">Actions</span></th>
                      </tr>
                    </thead>
                    <tbody>
                      {items.map((d) => {
                        const title = dramaName(d)
                        return (
                          <tr key={d.id} className={d.id === selectedId ? 'selected' : undefined}>
                            {selecting && (
                              <td className="check-col">
                                <label className="check-hit">
                                  <input
                                    type="checkbox"
                                    aria-label={`Select ${title}`}
                                    checked={!!checked?.has(d.id)}
                                    onChange={() => toggle(d.id)}
                                  />
                                </label>
                              </td>
                            )}
                            <td>
                              <a className="table-title" href={workspaceHref(d.id)}>{title}</a>
                              {d.title_en && d.title_zh && <div className="muted">{d.title_zh}</div>}
                            </td>
                            <td>{mediaTypeLabel(d.media_type)}</td>
                            <td>{languageLabel(d.source_language)}</td>
                            <td>{d.status && <Badge kind="status" value={d.status} />}</td>
                            <td>{d.custom_tags.join(', ')}</td>
                            <td className="row-actions">
                              <a className={buttonClass('ghost', 'sm')} href={readHref(d)} aria-label={`Read ${title}`}>Read</a>
                              <button type="button" className={buttonClass('ghost', 'sm')} onClick={() => onSelect(d.id)} aria-label={`Details: ${title}`}>
                                Details
                              </button>
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </>
      )}
    </section>
  )
}

function LineHits({ hits, error }: { hits: LibrarySearchHit[] | null; error: unknown }) {
  return (
    <div className="line-hits">
      <ErrorBanner error={error} />
      {hits === null && !error && <p className="muted">Type a word or phrase and press Enter to search every drama's lines.</p>}
      {hits && <p className="muted" data-testid="search-count">{hits.length} {hits.length === 1 ? 'match' : 'matches'}</p>}
      {hits && hits.length > 0 && (
        <ul className="line-hit-list">
          {hits.map((h) => (
            <li key={`${h.drama_id}-${h.idx}`}>
              <div className="line-hit-head">
                <a href={workspaceHref(h.drama_id, 'review')}>{dramaName({ ...h, id: h.drama_id })}</a>
                <span className="muted">Line {lineNumber(h.idx)}</span>
              </div>
              {h.zh && <p className="line-hit-zh">{h.zh}</p>}
              {h.en && <p className="muted">{h.en}</p>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function SkeletonGrid() {
  return (
    <ul className="drama-grid skeleton" aria-label="Loading dramas">
      {Array.from({ length: 6 }, (_, i) => (
        <li key={i} className="drama-card" aria-hidden="true">
          <div className="drama-tile" />
          <div className="drama-card-main"><span className="skeleton-line" /><span className="skeleton-line short" /></div>
        </li>
      ))}
    </ul>
  )
}
