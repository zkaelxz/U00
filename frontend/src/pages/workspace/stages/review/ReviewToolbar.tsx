import { useId, useRef, useState, type ReactNode, type RefObject } from 'react'

import { buttonClass } from '../../../../components/uiClasses'
import type { LineFilter } from '../../../../types/review'
import { gapChipLabel } from './gapModel'
import { chipLabel, parseJump } from './reviewLogic'

interface FilterCounts {
  all: number | null
  flagged: number
  untranslated: number
}

interface Props {
  isPhone: boolean
  // Scrolled phone: only the chip row stays (the player strip and the extra rows are hidden, not unmounted).
  collapsed?: boolean
  filter: LineFilter
  counts: FilterCounts | null
  // Stretches with no line (from the waveform's gap check); the chip jumps to the next one.
  gapCount: number
  onNextGap: () => void
  onPrevGap: () => void
  // 1-based position of the gap last jumped to (null before the first jump) and the typed jump.
  gapIndex: number | null
  onGoGap: (n: number) => void
  // Lines flagged "Source text looks off"; the chip opens their list.
  suspectCount: number
  suspectOpen: boolean
  onToggleSuspect: () => void
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
  compact: boolean
  onCompact: (v: boolean) => void
  player: ReactNode
}

const FILTERS: LineFilter[] = ['all', 'flagged', 'untranslated']
const FULL: Record<LineFilter, string> = { all: 'All lines', flagged: 'Flagged', untranslated: 'Untranslated' }

// A number you can type into: Enter or leaving the box jumps (clamped to 1..max), Escape puts the old value back.
function JumpInput({ value, max, label, describedBy, placeholder, className, onJump }: {
  value: string
  max: number
  label: string
  describedBy?: string
  placeholder?: string
  className: string
  onJump: (n: number) => void
}) {
  const [draft, setDraft] = useState<string | null>(null)
  // blur() inside the Escape handler runs onBlur with the stale draft; this stops it from jumping.
  const cancelled = useRef(false)
  const commit = () => {
    if (cancelled.current) {
      cancelled.current = false
      return
    }
    if (draft === null) return
    const n = parseJump(draft, max)
    setDraft(null)
    if (n !== null && String(n) !== value) onJump(n)
  }
  const shown = draft ?? value
  return (
    <input
      className={className}
      type="text"
      inputMode="numeric"
      autoComplete="off"
      enterKeyHint="go"
      aria-label={label}
      aria-describedby={describedBy}
      placeholder={placeholder}
      value={shown}
      style={{ width: `${Math.max(String(max).length, 1) + 0.5}ch` }}
      onFocus={(e) => e.currentTarget.select()}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          e.preventDefault()
          commit()
        } else if (e.key === 'Escape') {
          e.preventDefault()
          cancelled.current = draft !== null
          setDraft(null)
          e.currentTarget.blur()
        }
      }}
    />
  )
}

export function Pager({ page, pages, onPage, labelled }: { page: number; pages: number; onPage: (p: number) => void; labelled?: boolean }) {
  const totalId = useId()
  return (
    <span className="review-pager" role="group" aria-label="Pages">
      <button type="button" aria-label="Previous page" title="Previous page ([)" disabled={page <= 1} onClick={() => onPage(page - 1)}>
        ‹
      </button>
      <span data-testid={labelled ? 'page-label' : undefined} className="review-page-label">
        <JumpInput className="review-page-input" label="Page number" describedBy={totalId} value={String(page)} max={pages} onJump={onPage} />
        <span aria-hidden="true">/{pages}</span>
        <span id={totalId} className="sr-only">of {pages}. Type a number and press Enter to jump.</span>
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
  const [moreOpen, setMoreOpen] = useState(false)
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
      <button type="button" className={buttonClass('ghost', 'sm')} aria-expanded={p.replaceOpen} onClick={p.onToggleReplace}>
        Replace…
      </button>
      <button type="button" className={buttonClass('ghost', 'sm')} aria-pressed={p.compact} onClick={() => p.onCompact(!p.compact)}>
        Compact rows
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
      <button type="button" className={buttonClass('ghost', 'sm')} aria-haspopup="dialog" onClick={p.onKeys}>
        Shortcuts
      </button>
    </div>
  )
  const count =
    p.resultCount !== null ? (
      <span className="muted review-count" role="status" data-testid="search-count">
        {p.resultCount >= 200
          ? 'First 200 matches shown; more may exist. Type more of the line to narrow it.'
          : `${p.resultCount} match${p.resultCount === 1 ? '' : 'es'}`}
      </span>
    ) : null

  // Phones: Search and More are buttons on the chip row; each opens its own
  // row inside the toolbar box.
  return (
    <div className={p.collapsed ? 'review-toolbar is-collapsed' : 'review-toolbar'}>
      <div className="review-toolbar-row">
        {chips}
        {p.gapCount > 0 && (
          <span className="review-gap-step" role="group" aria-label="Gaps: stretches with no line">
            <button type="button" className="review-gap-arrow" aria-label="Previous gap" title="Previous stretch with no line" onClick={p.onPrevGap}>‹</button>
            <button type="button" className="review-chip review-gaps-chip" title="Jump to the next stretch with no line" onClick={p.onNextGap}>
              {gapChipLabel(p.gapCount)}
            </button>
            <JumpInput className="review-gap-input" label="Go to gap number" placeholder="#" value={p.gapIndex === null ? '' : String(p.gapIndex)} max={p.gapCount} onJump={p.onGoGap} />
            <button type="button" className="review-gap-arrow" aria-label="Next gap" title="Next stretch with no line" onClick={p.onNextGap}>›</button>
          </span>
        )}
        {p.suspectCount > 0 && (
          <button type="button" className="review-chip review-suspect-chip" aria-expanded={p.suspectOpen}
                  title="Lines whose transcription looks wrong or split badly" onClick={p.onToggleSuspect}>
            {p.isPhone ? `Source ${p.suspectCount}` : `Source looks off ${p.suspectCount}`}
          </button>
        )}
        {p.isPhone && !showSearch && (
          <button type="button" className="review-search-toggle" aria-label="Search lines" onClick={() => setSearchOpen(true)}>
            <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false">
              <circle cx="10.5" cy="10.5" r="6.5" fill="none" stroke="currentColor" strokeWidth="2" />
              <line x1="15.5" y1="15.5" x2="21" y2="21" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
          </button>
        )}
        {p.isPhone && (
          <button type="button" className={buttonClass('ghost', 'sm', 'review-more-toggle')} aria-expanded={moreOpen} onClick={() => setMoreOpen(!moreOpen)}>
            More
          </button>
        )}
        {!p.isPhone && searchBox}
        {!p.isPhone && count}
        {p.showPager && <Pager page={p.page} pages={p.pages} onPage={p.onPage} labelled />}
      </div>
      {p.isPhone && showSearch && (
        <div className="review-toolbar-row">
          {searchBox}
          {count}
        </div>
      )}
      {(!p.isPhone || moreOpen) && extras}
      {p.player}
    </div>
  )
}
