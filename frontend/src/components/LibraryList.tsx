import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react'

import { api } from '../api/client'
import { getFilterOptions, searchLines } from '../api/library'
import type { DramaSummary } from '../api/types'
import { nameLang } from '../hanScript'
import { usePersistedState } from '../hooks/usePersistedState'
import { useDetailsMenu } from '../hooks/useDetailsMenu'
import { useMediaQuery } from '../hooks/useMediaQuery'
import { languageLabel, mediaTypeLabel, statusLabel, tagLabel } from '../labels'
import { lineNumber } from '../lineNumber'
import { routeHref } from '../router'
import { MAX_SELECTION, allSelected, selectAllVisible, selectIds, toggleLibraryId, visibleTitles } from '../pages/libraryAdmin/libraryAdmin'
import { MEDIA_TYPES, SOURCE_LANGUAGES } from '../pages/libraryForm'
import {
  GROUP_BYS, TITLE_SORTS, groupTitles, isGroupBy, limitGroups, isTitleSort, type GroupBy, type TitleSort,
} from '../pages/titleOrder'
import type { LibraryFilterOptions, LibrarySearchHit } from '../types/library'
import { Badge } from './Badge'
import { Card } from './Card'
import { CoverGrid } from './CoverGrid'
import { DramaCards } from './DramaCards'
import { ErrorBanner } from './ErrorBanner'
import { isForbidden, LINES_READ_FORBIDDEN } from './errorMessages'
import './libraryParity.css'
import {
  NO_MORE_FILTERS, listQuery, moreFilterCount, toggleTag, withCurrent,
  type MoreFilters,
} from './libraryFilters'
import { countDramas, dramaName, hideLibraryBar, lineHitCount, lineHitHref, listDelay, matchesReading, READING_FILTERS, readAction, workspaceHref, type ReadingFilter } from './libraryView'
import { SharingControl } from './SharingControl'
import { Sheet } from './Sheet'
import { Paged, ShowMore } from './ShowMore'
import { useShowMore } from '../hooks/useShowMore'
import { buttonClass } from './uiClasses'
import { useLatest } from '../hooks/useLatest'

// Status and quick-filter choices; '' means no filter.
const STATUSES = ['', 'not started', 'aligned', 'translated', 'dubbed', 'exported']
const QUICK_FILTERS = ['', 'Favorite', 'On Hold', 'Plan to Translate']

type Scope = 'titles' | 'lines'
// 'grid' is the card list (tile left, text right); 'covers' is the big-cover shelf; 'list' is the desktop table.
type View = 'grid' | 'covers' | 'list'

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
  // Titles with a saved reading position (the Continue shelf's): cards offer Continue instead of Watch, Listen or Read.
  continuing?: ReadonlySet<number>
  // The titles on screen (expanded groups, display order), for pruning the selection to what the viewer can see.
  onVisible?: (items: DramaSummary[]) => void
  // The page shows a failed load once, with Retry; the list reports it instead of drawing its own line.
  onLoadError: (error: unknown) => void
  // Shown at the right end of the quick-filter row.
  chipsEnd?: ReactNode
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
  onRetrySeries, onVisible, onLoadError, continuing, chipsEnd,
}: Props) {
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')
  const [quickFilter, setQuickFilter] = useState('')
  const [reading, setReading] = useState<ReadingFilter>('')
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
  const [error, setError] = useState<unknown>(null)
  const [hits, setHits] = useState<LineHitList | null>(null)
  const [hitsError, setHitsError] = useState<unknown>(null)
  // A slow earlier search must not replace the hits of the one typed after it.
  const lineSearchSeq = useRef(0)
  const phone = useMediaQuery('(max-width: 640px)')
  const [filtersOpen, setFiltersOpen] = useState(false)
  const onItemsRef = useLatest(onItems)
  const onLoadErrorRef = useLatest(onLoadError)
  const onVisibleRef = useLatest(onVisible)
  const selecting = !!checked && !!onCheckedChange
  const toggle = (id: number) => checked && onCheckedChange?.(toggleLibraryId(checked, id))
  // The table needs width: a phone saved on List shows cards.
  const table = view === 'list' && !phone
  const covers = view === 'covers'
  const titleSearch = scope === 'titles' ? search : ''
  const filtered = !!(titleSearch.trim() || status || quickFilter || reading) || moreFilterCount(more) > 0
  // Hidden, not unmounted: the saved search, sort and view still apply once the first title exists.
  const emptyLibrary = scope === 'titles' && hideLibraryBar(items?.length ?? null, filtered)

  // The reload the last request went out under; null before the first.
  const lastReload = useRef<number | null>(null)
  useEffect(() => {
    let cancelled = false
    const timer = setTimeout(() => {
      lastReload.current = reloadKey
      api
        .listDramas(listQuery(titleSearch, status, quickFilter, more))
        .then((resp) => {
          if (!cancelled) {
            setItems(resp.items)
            setError(null)
            onLoadErrorRef.current(null)
            onItemsRef.current?.(resp.items)
          }
        })
        .catch((e: unknown) => {
          if (cancelled) return
          setError(e)
          onLoadErrorRef.current(e)
        })
    }, listDelay(lastReload.current, reloadKey))
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [titleSearch, status, quickFilter, more, reloadKey, onItemsRef, onLoadErrorRef])

  // The reading filter is applied here, not by the server: the list already carries what it needs.
  const shown = useMemo(() => (items && reading ? items.filter((d) => matchesReading(d, reading)) : items), [items, reading])
  const allGroups = shown ? groupTitles(shown, groupBy, sort, series ?? []) : []
  // A thousand cards on one page is slow to draw and endless to scroll; the rest come with Show more. The limit comes
  // back to one page when the list is filtered, searched or regrouped.
  const page = useShowMore(`${titleSearch}|${status}|${quickFilter}|${reading}|${groupBy}|${sort}|${JSON.stringify(more)}`)
  const groups = limitGroups(allGroups, page.limit)
  const totals = new Map(allGroups.map((g) => [g.key, g.items.length]))
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
    const seq = ++lineSearchSeq.current
    searchLines(term).then(
      (r) => { if (seq === lineSearchSeq.current) { setHits({ term, items: r.items, more: !!r.more }); setHitsError(null) } },
      (err: unknown) => { if (seq === lineSearchSeq.current) setHitsError(err) },
    )
  }

  const clearFilters = () => {
    setSearch('')
    setStatus('')
    setQuickFilter('')
    setReading('')
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
  const listView = (list: DramaSummary[], label = '') => covers ? (
    <CoverGrid
      items={list}
      selectedId={selectedId}
      onSelect={onSelect}
      selectMode={selecting && selectMode}
      checked={checked}
      onToggle={toggle}
      continuing={continuing}
    />
  ) : !table ? (
    <DramaCards
      items={list}
      selectedId={selectedId}
      onSelect={onSelect}
      selectMode={selecting && selectMode}
      checked={checked}
      onToggle={toggle}
      continuing={continuing}
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
                        <th className="title-col">Title</th>
                        <th>Type</th>
                        <th>Language</th>
                        <th>Released</th>
                        <th>Status</th>
                        <th className="tags-col">Tags</th>
                        <th><span className="visually-hidden">Actions</span></th>
                      </tr>
                    </thead>
                    <tbody>
                      {list.map((d) => {
                        const title = dramaName(d)
                        const read = readAction(d)
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
                            <td className="title-col">
                              <a className="table-title" href={workspaceHref(d.id)} lang={nameLang(title, d)}>{title}</a>
                              {d.title_en && d.title_zh && <div className="muted" lang={nameLang(d.title_zh, d)}>{d.title_zh}</div>}
                              <SharingControl kind="drama" id={d.id} title={title} isPrivate={d.is_private} ownedByMe={d.owned_by_me} seriesId={d.series_id} />
                            </td>
                            <td>{mediaTypeLabel(d.media_type)}</td>
                            <td>{languageLabel(d.source_language)}</td>
                            <td>{d.release_date}</td>
                            <td>{d.status && <Badge kind="status" value={d.status} />}</td>
                            <td className="tags-col">{d.custom_tags.join(', ')}</td>
                            <td className="row-actions">
                              <a className={buttonClass('ghost', 'sm')} href={read.href} aria-label={`${read.label} ${title}`}>{read.label}</a>
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

  const activeFilters = (status ? 1 : 0) + (reading ? 1 : 0) + moreFilterCount(more)
  const statusSelects = (
    <div className="toolbar-filters">
      <select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
        {STATUSES.map((s) => (
          <option key={s} value={s}>{s ? statusLabel(s) : 'Any status'}</option>
        ))}
      </select>
      <select aria-label="Reading" value={reading} onChange={(e) => setReading(e.target.value as ReadingFilter)}>
        {READING_FILTERS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
      </select>
    </div>
  )
  const moreFilters = <MoreFiltersFold value={more} onChange={setMore} reloadKey={reloadKey} />
  const filtersMenu = (
    <FiltersMenu count={activeFilters}>
      {(open) => (
        <>
          {statusSelects}
          <MoreFilterFields value={more} onChange={setMore} reloadKey={reloadKey} active={open} />
        </>
      )}
    </FiltersMenu>
  )
  const scopeControl = (
    <Segmented<Scope>
      label="Search in" name="library-scope" value={scope} onChange={setScope}
      options={[['titles', 'Titles'], ['lines', 'Lines']]}
    />
  )
  const sortIcon = <SortIcon sort={sort} onSort={setSort} />
  const groupSelect = (
    <select aria-label="Group by" value={shownGroup} onChange={(e) => setGroupBy(e.target.value)}>
      {GROUP_BYS.map(([id, label]) => <option key={id} value={id}>{id === 'none' ? 'No grouping' : `Group by ${label.toLowerCase()}`}</option>)}
    </select>
  )
  const quickChips = scope === 'titles' && !emptyLibrary && (
    <div className="quick-chips" role="group" aria-label="Quick filter">
      {QUICK_FILTERS.map((q) => (
        <button key={q} type="button" className="quick-chip" aria-pressed={quickFilter === q} onClick={() => setQuickFilter(q)}>
          {q ? tagLabel(q) : 'All titles'}
        </button>
      ))}
    </div>
  )
  const searchBox = <SearchBox lines={scope === 'lines'} value={search} onChange={setSearch} />
  const viewControl = (
    <Segmented<View>
      label="View" name="library-view" value={phone && view === 'list' ? 'grid' : view} onChange={setView}
      options={phone ? [['grid', 'Cards'], ['covers', 'Covers']] : [['grid', 'Cards'], ['covers', 'Covers'], ['list', 'List']]}
    />
  )

  return (
    <section className="library-list" aria-labelledby="library-heading">
      <h2 id="library-heading" className="visually-hidden">Titles</h2>
      <form className="library-toolbar" role="search" onSubmit={submit} hidden={emptyLibrary}>
        {phone ? searchBox : (
          <div className="toolbar-row">
            {searchBox}
            {scopeControl}
            {scope === 'titles' && (
              <>
                {filtersMenu}
                {sortIcon}
                {groupSelect}
              </>
            )}
            <div className="toolbar-end">
              {scope === 'titles' && viewControl}
              {selectControls}
            </div>
          </div>
        )}
        {phone && (
          <div className="toolbar-row">
            <button type="button" className={buttonClass('secondary')} onClick={() => setFiltersOpen(true)}>
              Filters{activeFilters > 0 && ` (${activeFilters})`}
            </button>
            {scope === 'titles' && sortIcon}
            {scope === 'titles' && viewControl}
            <div className="toolbar-end">{selectControls}</div>
          </div>
        )}
      </form>
      {(quickChips || chipsEnd) && (
        <div className="quick-row">
          {quickChips}
          {chipsEnd}
        </div>
      )}
      <Sheet open={phone && filtersOpen} title="Filters" onClose={() => setFiltersOpen(false)}>
        <div className="filter-sheet">
          {scopeControl}
          {scope === 'titles' && (
            <>
              {statusSelects}
              {groupSelect}
              {moreFilters}
            </>
          )}
        </div>
      </Sheet>

      {scope === 'lines' ? (
        <LineHits hits={hits} error={hitsError} />
      ) : (
        <>
          {wantedGroup === 'series' && series === null && seriesError && (
            <p className="status-line warn" role="status" data-testid="series-load-failed">
              <span>Series could not be loaded; showing the plain list.</span>
              {onRetrySeries && <button type="button" className={buttonClass('ghost', 'sm')} onClick={onRetrySeries}>Retry</button>}
            </p>
          )}
          {!error && items === null && <SkeletonGrid />}
          {!error && shown && (
            <>
              <p className="muted list-count" data-testid="drama-count">{countDramas(shown.length)}</p>
              {selecting && shown.length > MAX_SELECTION && (
                <p className="muted">At most {MAX_SELECTION} at a time.</p>
              )}
              {/* A member's empty library is explained once, by the page's own note. */}
              {shown.length === 0 && !filtered && onCreate && (
                <Card title="No titles yet" meta="Add one to start transcribing, translating or reading.">
                  <div className="actions">
                    {/* The page's own + Add (a phone's bar) is the primary one. */}
                    <button type="button" className={buttonClass('secondary')} onClick={onCreate}>Add</button>
                  </div>
                </Card>
              )}
              {shown.length === 0 && filtered && (
                <div className="empty-note">
                  <p>No titles match.</p>
                  <button type="button" className={buttonClass('ghost', 'sm')} onClick={clearFilters}>Clear filters</button>
                </div>
              )}
              {shown.length > 0 && groups.map((g) => (
                g.label === '' ? <div key={g.key}>{listView(g.items)}</div> : (
                  <section key={g.key} className="title-group" aria-label={g.label} data-testid="title-group">
                    <h3 className="title-group-head">
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
                        <span className="muted">{(totals.get(g.key) ?? g.items.length) === 1 ? '1 title' : `${totals.get(g.key) ?? g.items.length} titles`}{g.mix && ` · ${g.mix}`}</span>
                      </button>
                      {g.seriesId !== undefined && (
                        <a className="title-group-link" href={routeHref({ name: 'series', id: g.seriesId })} aria-label={`Open series ${g.label}`}>Open series</a>
                      )}
                    </h3>
                    {!collapsed.has(g.key) && listView(g.items, g.label)}
                  </section>
                )
              ))}
              {shown.length > 0 && <ShowMore shown={groups.reduce((n, g) => n + g.items.length, 0)} total={shown.length} onMore={page.more} testId="library-more" />}
            </>
          )}
        </>
      )}
    </section>
  )
}

type LineHitList = { term: string; items: LibrarySearchHit[]; more: boolean }

function LineHits({ hits, error }: { hits: LineHitList | null; error: unknown }) {
  return (
    <div className="line-hits">
      {isForbidden(error) ? <p className="muted" role="status">{LINES_READ_FORBIDDEN}</p> : <ErrorBanner error={error} />}
      {hits === null && !error && <p className="muted">Type a word or phrase and press Enter to search every title's lines.</p>}
      {hits && <p className="muted" data-testid="search-count">{lineHitCount({ count: hits.items.length, more: hits.more })}</p>}
      {hits && hits.items.length > 0 && (
        <Paged items={hits.items} testId="line-hits-more">
          {(visible) => (
        <ul className="line-hit-list">
          {visible.map((h) => (
            <li key={`${h.drama_id}-${h.idx}`}>
              <div className="line-hit-head">
                <a href={lineHitHref(h.drama_id, hits.term)}>{dramaName({ ...h, id: h.drama_id })}</a>
                <span className="muted">Line {lineNumber(h.idx)}</span>
              </div>
              {h.zh && <p className="line-hit-zh">{h.zh}</p>}
              {h.en && <p className="muted">{h.en}</p>}
            </li>
          ))}
        </ul>
          )}
        </Paged>
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

// The select sits invisibly over the icon, so a tap opens the platform's own picker.
function SortIcon({ sort, onSort }: { sort: TitleSort; onSort: (next: string) => void }) {
  return (
    <label className="sort-icon" title="Sort order">
      <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M8 20V4M4 8l4-4 4 4M16 4v16M12 16l4 4 4-4" />
      </svg>
      <select aria-label="Sort by" value={sort} onChange={(e) => onSort(e.target.value)}>
        {TITLE_SORTS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
      </select>
    </label>
  )
}

function SearchBox({ lines, value, onChange }: { lines: boolean; value: string; onChange: (next: string) => void }) {
  const label = lines ? 'Search all lines' : 'Search titles, people, tags and series'
  return (
    <div className="toolbar-search">
      <input
        type="search"
        placeholder={lines ? 'Search every line, then press Enter' : label}
        aria-label={label}
        maxLength={200}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      {lines && <button type="submit" className={buttonClass('secondary')}>Search</button>}
    </div>
  )
}

// Status, reading state and the rest of the filters in one menu, so the toolbar stays one row on a computer.
function FiltersMenu({ count, children }: { count: number; children: (open: boolean) => ReactNode }) {
  const ref = useDetailsMenu()
  const [open, setOpen] = useState(false)
  return (
    <details ref={ref} className="filters-menu" onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}>
      <summary className={buttonClass('secondary')}>Filters{count > 0 && ` (${count})`}</summary>
      <div className="filters-menu-panel" role="group" aria-label="Filters">{children(open)}</div>
    </details>
  )
}

// The phone sheet's fold around the same fields.
function MoreFiltersFold({ value, onChange, reloadKey }: {
  value: MoreFilters
  onChange: (next: MoreFilters) => void
  reloadKey: number
}) {
  const [open, setOpen] = useState(false)
  const n = moreFilterCount(value)
  return (
    <details className="more-filters" onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}>
      <summary>More filters{n > 0 && ` (${n})`}</summary>
      <MoreFilterFields value={value} onChange={onChange} reloadKey={reloadKey} active={open} />
    </details>
  )
}

// Studio, author, voice actor, language, type and custom tags. The data-driven choices load when the fields are
// first shown (`active`), and again after a create/delete.
function MoreFilterFields({ value, onChange, reloadKey, active }: {
  value: MoreFilters
  onChange: (next: MoreFilters) => void
  reloadKey: number
  active: boolean
}) {
  const [options, setOptions] = useState<LibraryFilterOptions | null>(null)
  const [error, setError] = useState(false)
  useEffect(() => {
    if (!active) return
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
  }, [active, reloadKey])

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
    <>
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
    </>
  )
}
