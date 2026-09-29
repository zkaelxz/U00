import { useState, type ReactNode, type RefObject } from 'react'

import type { LineFilter } from '../../../../types/review'
import { chipLabel } from './reviewLogic'

export interface FilterCounts {
  all: number | null
  flagged: number
  untranslated: number
}

interface Props {
  isPhone: boolean
  filter: LineFilter
  counts: FilterCounts | null
  onFilter: (f: LineFilter) => void
  search: string
  searchRef: RefObject<HTMLInputElement | null>
  onSearch: (v: string) => void
  resultCount: number | null
  page: number
  pages: number
  showPager: boolean
  onPage: (p: number) => void
  onGoTo: (n: number) => void
  replaceOpen: boolean
  onToggleReplace: () => void
  onKeys: () => void
  player: ReactNode
}

const FILTERS: LineFilter[] = ['all', 'flagged', 'untranslated']
const FULL: Record<LineFilter, string> = { all: 'All lines', flagged: 'Flagged', untranslated: 'Untranslated' }

export function Pager({ page, pages, onPage, labelled }: { page: number; pages: number; onPage: (p: number) => void; labelled?: boolean }) {
  return (
    <span className="review-pager" role="group" aria-label="Pages">
      <button type="button" aria-label="Previous page" title="Previous page ([)" disabled={page <= 1} onClick={() => onPage(page - 1)}>
        ‹
      </button>
      <span data-testid={labelled ? 'page-label' : undefined} className="review-page-label">
        <span aria-hidden="true">{page}/{pages}</span>
        <span className="sr-only">Page {page} of {pages}</span>
      </span>
      <button type="button" aria-label="Next page" title="Next page (])" disabled={page >= pages} onClick={() => onPage(page + 1)}>
        ›
      </button>
    </span>
  )
}

// Sticky Review toolbar: filter chips with whole-drama counts, search as you
// type, pager, Go to #, Replace… and the shortcut list; the player strip sits
// under it when the drama has media.
export function ReviewToolbar({ searchRef, ...p }: Props) {
  const [searchOpen, setSearchOpen] = useState(false)
  const [goTo, setGoTo] = useState('')
  const showSearch = !p.isPhone || searchOpen || p.search !== ''

  const chips = (
    <div className="review-chips" role="radiogroup" aria-label="Show">
      {FILTERS.map((f) => {
        const count = !p.counts ? null : f === 'all' ? p.counts.all : f === 'flagged' ? p.counts.flagged : p.counts.untranslated
        return (
          <label key={f} className="review-chip">
            <input
              type="radio"
              aria-label={count === null ? FULL[f] : `${FULL[f]} ${count}`}
              name="review-filter"
              value={f}
              checked={p.filter === f}
              onChange={() => p.onFilter(f)}
            />
            <span aria-hidden="true">{chipLabel(f, count, p.isPhone)}</span>
          </label>
        )
      })}
    </div>
  )

  const searchBox = (
    <div className="review-search" role="search">
      <input
        ref={searchRef}
        type="search"
        autoFocus={p.isPhone && searchOpen}
        aria-label="Search lines"
        placeholder="Search source or translation"
        value={p.search}
        onChange={(e) => p.onSearch(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Escape' && p.search) {
            e.preventDefault()
            p.onSearch('')
          }
        }}
      />
      {p.search && (
        <button type="button" className="review-clear" aria-label="Clear search" onClick={() => p.onSearch('')}>
          ×
        </button>
      )}
    </div>
  )

  const extras = (
    <div className="review-toolbar-row review-extras">
      <button type="button" aria-expanded={p.replaceOpen} onClick={p.onToggleReplace}>
        Replace…
      </button>
      <form
        className="review-goto"
        onSubmit={(e) => {
          e.preventDefault()
          const n = Number(goTo)
          if (goTo.trim() !== '' && Number.isInteger(n)) p.onGoTo(n)
        }}
      >
        <label>
          <span>Go to #</span>
          <input inputMode="numeric" aria-label="Go to line number" value={goTo} onChange={(e) => setGoTo(e.target.value)} />
        </label>
      </form>
      <button type="button" aria-haspopup="dialog" onClick={p.onKeys}>
        Keys ?
      </button>
      {p.resultCount !== null && (
        <span className="muted" role="status" data-testid="search-count">
          {p.resultCount} match{p.resultCount === 1 ? '' : 'es'}
          {p.resultCount >= 200 ? ' (max 200)' : ''}
        </span>
      )}
    </div>
  )

  return (
    <>
      <div className="review-toolbar">
        <div className="review-toolbar-row">
          {chips}
          {p.isPhone && !showSearch && (
            <button type="button" className="review-search-toggle" aria-label="Search lines" onClick={() => setSearchOpen(true)}>
              ⌕
            </button>
          )}
          {!p.isPhone && searchBox}
          {p.showPager && <Pager page={p.page} pages={p.pages} onPage={p.onPage} labelled />}
        </div>
        {p.isPhone && showSearch && <div className="review-toolbar-row">{searchBox}</div>}
        {!p.isPhone && extras}
        {p.player}
      </div>
      {p.isPhone && extras}
    </>
  )
}
