import { useEffect, useRef, useState, type FormEvent } from 'react'

import { api, ApiError } from '../api/client'
import { getFilterOptions, searchLines } from '../api/library'
import type { DramaSummary } from '../api/types'
import { usePersistedState } from '../hooks/usePersistedState'
import { useMediaQuery } from '../hooks/useMediaQuery'
import { languageLabel, mediaTypeLabel, statusLabel, tagLabel } from '../labels'
import { lineNumber } from '../lineNumber'
import { routeHref } from '../router'
import { MAX_SELECTION, allSelected, selectAllVisible, selectIds, toggleLibraryId, visibleTitles } from '../pages/libraryAdmin/libraryAdmin'
import { MEDIA_TYPES, SOURCE_LANGUAGES } from '../pages/libraryForm'
import {
  GROUP_BYS, TITLE_SORTS, groupTitles, isGroupBy, isTitleSort, type GroupBy, type TitleSort,
} from '../pages/titleOrder'
import type { LibraryFilterOptions, LibrarySearchHit } from '../types/library'
import { Badge } from './Badge'
import { Card } from './Card'
import { DramaCards } from './DramaCards'
import { ErrorBanner } from './ErrorBanner'
import { isForbidden, LINES_READ_FORBIDDEN } from './errorMessages'
import './libraryParity.css'
import {
  NO_MORE_FILTERS, listQuery, moreFilterCount, toggleTag, withCurrent,
  type MoreFilters,
} from './libraryFilters'
import { countDramas, dramaName, hideLibraryBar, readHref, workspaceHref } from './libraryView'
import { SharingControl } from './SharingControl'
import { Sheet } from './Sheet'
import { buttonClass } from './uiClasses'

// Status and quick-filter choices; '' means no filter.
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
  // The viewer's visible series (GET /api/series); null until loaded or if it failed.
  series?: { id: number; name: string }[] | null
  // GET /api/series failed: grouping by series falls back to the plain list, with a Retry.
  seriesError?: boolean
  onRetrySeries?: () => void
  // The titles on screen (expanded groups, display order), for pruning the selection to what the viewer can see.
  onVisible?: (items: DramaSummary[]) => void
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
  selectedId, onSelect, reloadKey = 0, checked, onCheckedChange, selectMode, onSelectModeChange, onItems, onCreate, series = null, seriesError = false,
  onRetrySeries, onVisible,
}: Props) {
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')
  const [quickFilter, setQuickFilter] = useState('')
  const [scope, setScope] = useState<Scope>('titles')
  const [view, setView] = usePersistedState<View>('library.view', 'grid')
  const [more, setMore] = useState<MoreFilters>(NO_MORE_FILTERS)
  // Remembered in this browser; the suffix is the storage version, bumped if the meaning of a value changes.
  const [storedSort, setSort] = usePersistedState<string>('library.sort.v1', 'added')
  const [storedGroup, setGroupBy] = usePersistedState<string>('library.groupBy.v1', 'none')
  const [collapsedKeys, setCollapsedKeys] = usePersistedState<string[]>('library.groupsCollapsed.v1', [])
  const sort: TitleSort = isTitleSort(storedSort) ? storedSort : 'added'
  const wantedGroup: GroupBy = isGroupBy(storedGroup) ? storedGroup : 'none'
  // Series names come from a second request: until it lands, list ungrouped rather than
  // filing every title under "No series".
  const groupBy: GroupBy = wantedGroup === 'series' && series === null ? 'none' : wantedGroup
  // The select shows what is actually applied once the series list is known to have failed.
  const shownGroup: GroupBy = wantedGroup === 'series' && series === null && seriesError ? 'none' : wantedGroup
  const collapsed = new Set(Array.isArray(collapsedKeys) ? collapsedKeys : [])
  const toggleGroup = (key: string) => {
    const next = new Set(collapsed)
    if (!next.delete(key)) next.add(key)
    setCollapsedKeys([...next])
  }
  const [items, setItems] = useState<DramaSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [hits, setHits] = useState<LibrarySearchHit[] | null>(null)
  const [hitsError, setHitsError] = useState<unknown>(null)
  const phone = useMediaQuery('(max-width: 640px)')
  const [filtersOpen, setFiltersOpen] = useState(false)
  const onItemsRef = useRef(onItems)
  const onVisibleRef = useRef(onVisible)
  useEffect(() => {
    onItemsRef.current = onItems
    onVisibleRef.current = onVisible
  })
  const selecting = !!checked && !!onCheckedChange
  const toggle = (id: number) => checked && onCheckedChange?.(toggleLibraryId(checked, id))
  // Phones always get cards; List view is the desktop table.
  const table = view === 'list' && !phone
  const titleSearch = scope === 'titles' ? search : ''
  const filtered = !!(titleSearch.trim() || status || quickFilter) || moreFilterCount(more) > 0
  // Hidden, not unmounted: the saved search, sort and view still apply once the first title exists.
  const emptyLibrary = scope === 'titles' && hideLibraryBar(items?.length ?? null, filtered)

  useEffect(() => {
    let cancelled = false
    // Debounced so typing in the search box doesn't send a request per key.
    const timer = setTimeout(() => {
      api
        .listDramas(listQuery(titleSearch, status, quickFilter, more))
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
  }, [titleSearch, status, quickFilter, more, reloadKey])

  const groups = items ? groupTitles(items, groupBy, sort, series ?? []) : []
  // Select all, the bulk-action count and delete only ever see what is on screen: a collapsed group's titles are not.
  const visible = visibleTitles(groups, collapsed)
  const allChecked = !!checked && allSelected(checked, visible)
  const toggleAll = () => onCheckedChange?.(allChecked ? new Set() : selectAllVisible(visible).ids)
  const visibleKey = visible.map((d) => d.id).join(',')
  useEffect(() => {
    if (items) onVisibleRef.current?.(visible)
    // visibleKey stands for the list's contents.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visibleKey, items === null])

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
    setMore(NO_MORE_FILTERS)
  }

  // Select mode works the same in both views: the cards turn into
  // checkboxes, or the List view shows its checkbox column.
  const checking = selecting && !!selectMode
  const selectControls = selecting && items && items.length > 0 && scope === 'titles' && (
    selectMode ? (
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
    )
  )

  // One group's titles (or the whole list): cards, or on desktop the table.
  const listView = (list: DramaSummary[], label = '') => !table ? (
    <DramaCards
      items={list}
      selectedId={selectedId}
      onSelect={onSelect}
      selectMode={selecting && selectMode}
      checked={checked}
      onToggle={toggle}
    />
  ) : (
                <div className="table-scroll drama-table">
                  <table>
                    <thead>
                      <tr>
                        {checking && (
                          <th className="check-col">
                            <label className="check-hit">
                              <input type="checkbox" aria-label={label ? `Select all visible in ${label}` : 'Select all visible'} checked={!!checked && allSelected(checked, list)} onChange={() => checked && onCheckedChange?.(selectIds(checked, list))} />
                            </label>
                          </th>
                        )}
                        <th>Title</th>
                        <th>Type</th>
                        <th>Language</th>
                        <th>Released</th>
                        <th>Status</th>
                        <th>Tags</th>
                        <th><span className="visually-hidden">Actions</span></th>
                      </tr>
                    </thead>
                    <tbody>
                      {list.map((d) => {
                        const title = dramaName(d)
                        return (
                          <tr key={d.id} className={d.id === selectedId ? 'selected' : undefined}>
                            {checking && (
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
                              <SharingControl kind="drama" id={d.id} title={title} isPrivate={d.is_private} ownedByMe={d.owned_by_me} seriesId={d.series_id} />
                            </td>
                            <td>{mediaTypeLabel(d.media_type)}</td>
                            <td>{languageLabel(d.source_language)}</td>
                            <td>{d.release_date}</td>
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
  )

  const activeFilters = (status ? 1 : 0) + (quickFilter ? 1 : 0) + moreFilterCount(more)
  const statusSelects = (
    <div className="toolbar-filters">
      <select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
        {STATUSES.map((s) => (
          <option key={s} value={s}>{s ? statusLabel(s) : 'Any status'}</option>
        ))}
      </select>
      <select aria-label="Quick filter" value={quickFilter} onChange={(e) => setQuickFilter(e.target.value)}>
        {QUICK_FILTERS.map((q) => (
          <option key={q} value={q}>{q ? tagLabel(q) : 'All titles'}</option>
        ))}
      </select>
    </div>
  )
  const moreFilters = <MoreFiltersFold value={more} onChange={setMore} reloadKey={reloadKey} />
  const scopeControl = (
    <Segmented<Scope>
      label="Search in" name="library-scope" value={scope} onChange={setScope}
      options={[['titles', 'Titles'], ['lines', 'Lines']]}
    />
  )
  const sortSelects = (
    <>
      <select aria-label="Sort by" value={sort} onChange={(e) => setSort(e.target.value)}>
        {TITLE_SORTS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
      </select>
      <select aria-label="Group by" value={shownGroup} onChange={(e) => setGroupBy(e.target.value)}>
        {GROUP_BYS.map(([id, label]) => <option key={id} value={id}>{id === 'none' ? 'No grouping' : `Group by ${label.toLowerCase()}`}</option>)}
      </select>
    </>
  )
  const viewControl = (
    <Segmented<View>
      label="View" name="library-view" value={view} onChange={setView}
      options={[['grid', 'Grid'], ['list', 'List']]}
    />
  )

  return (
    <section className="library-list" aria-labelledby="library-heading">
      <h3 id="library-heading" className="visually-hidden">Titles</h3>
      <form className="library-toolbar" role="search" onSubmit={submit} hidden={emptyLibrary}>
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
        {phone ? (
          <div className="toolbar-row">
            <button type="button" className={buttonClass('secondary')} onClick={() => setFiltersOpen(true)}>
              Filters{activeFilters > 0 && ` (${activeFilters})`}
            </button>
            <div className="toolbar-end">{selectControls}</div>
          </div>
        ) : (
          <>
            {scope === 'titles' && statusSelects}
            {scope === 'titles' && moreFilters}
            <div className="toolbar-row">
              {scopeControl}
              {scope === 'titles' && (
                <>
                  {sortSelects}
                  {viewControl}
                </>
              )}
              <div className="toolbar-end">{selectControls}</div>
            </div>
          </>
        )}
      </form>
      <Sheet open={phone && filtersOpen} title="Filters" onClose={() => setFiltersOpen(false)}>
        <div className="filter-sheet">
          {scopeControl}
          {scope === 'titles' && (
            <>
              {statusSelects}
              {sortSelects}
              {moreFilters}
            </>
          )}
        </div>
      </Sheet>

      {scope === 'lines' ? (
        <LineHits hits={hits} error={hitsError} />
      ) : (
        <>
          {error && <p className="error" role="alert">{error}</p>}
          {wantedGroup === 'series' && series === null && seriesError && (
            <p className="status-line warn" role="status" data-testid="series-load-failed">
              <span>Series could not be loaded; showing the plain list.</span>
              {onRetrySeries && <button type="button" className={buttonClass('ghost', 'sm')} onClick={onRetrySeries}>Retry</button>}
            </p>
          )}
          {!error && items === null && <SkeletonGrid />}
          {!error && items !== null && (
            <>
              <p className="muted list-count" data-testid="drama-count">{countDramas(items.length)}</p>
              {selecting && items.length > MAX_SELECTION && (
                <p className="muted">At most {MAX_SELECTION} at a time.</p>
              )}
              {/* A member's empty library is explained once, by the page's own note. */}
              {items.length === 0 && !filtered && onCreate && (
                <Card title="No titles yet" meta="Add one to start transcribing, translating or reading.">
                  <div className="actions">
                    <button type="button" className={buttonClass('secondary')} onClick={onCreate}>New title</button>
                  </div>
                </Card>
              )}
              {items.length === 0 && filtered && (
                <div className="empty-note">
                  <p>No titles match.</p>
                  <button type="button" className={buttonClass('ghost', 'sm')} onClick={clearFilters}>Clear filters</button>
                </div>
              )}
              {items.length > 0 && groups.map((g) => (
                g.label === '' ? <div key={g.key}>{listView(g.items)}</div> : (
                  <section key={g.key} className="title-group" aria-label={g.label} data-testid="title-group">
                    <h4 className="title-group-head">
                      {checking && !collapsed.has(g.key) && (
                        <label className="check-hit">
                          <input
                            type="checkbox"
                            aria-label={`Select all in ${g.label}`}
                            checked={!!checked && allSelected(checked, g.items)}
                            onChange={() => checked && onCheckedChange?.(selectIds(checked, g.items))}
                          />
                        </label>
                      )}
                      <button
                        type="button"
                        className="title-group-toggle"
                        aria-expanded={!collapsed.has(g.key)}
                        onClick={() => toggleGroup(g.key)}
                      >
                        <span aria-hidden="true">{collapsed.has(g.key) ? '▸' : '▾'}</span>
                        <span className="title-group-name">{g.label}</span>
                        <span className="muted">{g.items.length === 1 ? '1 title' : `${g.items.length} titles`}{g.mix && ` · ${g.mix}`}</span>
                      </button>
                      {g.seriesId !== undefined && (
                        <a className="title-group-link" href={routeHref({ name: 'series', id: g.seriesId })} aria-label={`Open series ${g.label}`}>Open series</a>
                      )}
                    </h4>
                    {!collapsed.has(g.key) && listView(g.items, g.label)}
                  </section>
                )
              ))}
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
      {isForbidden(error) ? <p className="muted" role="status">{LINES_READ_FORBIDDEN}</p> : <ErrorBanner error={error} />}
      {hits === null && !error && <p className="muted">Type a word or phrase and press Enter to search every title's lines.</p>}
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

// Built from the loaded card's own classes (title, original title, type, pills, footer buttons) with
// the text hidden, so the grid keeps its height when the titles arrive instead of jumping.
function SkeletonGrid() {
  return (
    <div role="status" aria-busy="true">
      <span className="visually-hidden">Loading titles…</span>
      <ul className="drama-grid skeleton" aria-label="Loading titles">
        {Array.from({ length: 6 }, (_, i) => (
          <li key={i} className="drama-card" aria-hidden="true">
            <div className="drama-tile" />
            <div className="drama-card-main">
              <span className="drama-card-title skeleton-text">Title</span>
              <p className="drama-card-orig skeleton-text short">Original</p>
              <p className="drama-card-type skeleton-text short">Type</p>
              <div className="pill-row"><Badge>Status</Badge><Badge>Language</Badge></div>
              <div className="drama-card-foot">
                <span className={buttonClass('ghost', 'sm')}>Read</span>
                <span className={buttonClass('ghost', 'sm')}>Details</span>
              </div>
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}

// Studio, author, voice actor, language, type and custom tags. The data-driven
// choices load when the fold is first opened (and again after a create/delete).
function MoreFiltersFold({ value, onChange, reloadKey }: {
  value: MoreFilters
  onChange: (next: MoreFilters) => void
  reloadKey: number
}) {
  const [open, setOpen] = useState(false)
  const [options, setOptions] = useState<LibraryFilterOptions | null>(null)
  const [error, setError] = useState(false)
  useEffect(() => {
    if (!open) return
    let cancelled = false
    getFilterOptions().then(
      (o) => {
        if (!cancelled) {
          setOptions(o)
          setError(false)
        }
      },
      () => !cancelled && setError(true),
    )
    return () => {
      cancelled = true
    }
  }, [open, reloadKey])

  const n = moreFilterCount(value)
  const set = (key: Exclude<keyof MoreFilters, 'tags'>) => (e: { target: { value: string } }) =>
    onChange({ ...value, [key]: e.target.value })
  const pick = (label: string, key: 'studio' | 'author' | 'voice_actor', choices: string[] | undefined) => (
    <select aria-label={label} value={value[key]} onChange={set(key)}>
      <option value="">Any {label.toLowerCase()}</option>
      {withCurrent(choices ?? [], value[key]).map((c) => (
        <option key={c} value={c}>{c}</option>
      ))}
    </select>
  )
  return (
    <details className="more-filters" onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}>
      <summary>More filters{n > 0 && ` (${n})`}</summary>
      <div className="filters">
        {pick('Studio', 'studio', options?.studios)}
        {pick('Author', 'author', options?.authors)}
        {pick('Voice actor', 'voice_actor', options?.voice_actors)}
        <select aria-label="Language" value={value.source_language} onChange={set('source_language')}>
          <option value="">Any language</option>
          {SOURCE_LANGUAGES.map((l) => (
            <option key={l} value={l}>{languageLabel(l)}</option>
          ))}
        </select>
        <select aria-label="Type" value={value.media_type} onChange={set('media_type')}>
          <option value="">Any type</option>
          {withCurrent(MEDIA_TYPES, value.media_type).map((t) => (
            <option key={t} value={t}>{mediaTypeLabel(t)}</option>
          ))}
        </select>
      </div>
      {options && options.custom_tags.length > 0 && (
        <fieldset className="tag-filter">
          <legend>Custom tags (all must match)</legend>
          {options.custom_tags.map((t) => (
            <label key={t} className="check">
              <input
                type="checkbox"
                checked={value.tags.includes(t)}
                onChange={() => onChange({ ...value, tags: toggleTag(value.tags, t) })}
              />
              {t}
            </label>
          ))}
        </fieldset>
      )}
      {error && <p className="muted">Couldn't load the studio, author, voice actor and tag choices.</p>}
      {n > 0 && (
        <button type="button" className="link" onClick={() => onChange(NO_MORE_FILTERS)}>
          Clear these filters
        </button>
      )}
    </details>
  )
}
