/** The kept places in Review's line list: the view, page and line it opens on for a title and on a switch of view, the sentence and highlight that say so, keeping the lines the person moves to, and holding a reopened row in view. */
import { useEffect, useRef, useState, type Dispatch, type MutableRefObject, type RefObject, type SetStateAction } from 'react'

import { listAllLines } from '../../../../api/restructure'
import { browserStorage, readPref, writePref } from '../../../../hooks/usePersistedState'
import { useRoute } from '../../../../router'
import type { LineFilter, ReviewLine } from '../../../../types/review'
import type { Pending } from './linesController'
import { useInitialLinesFilter } from './useCheckQueue'
import { ownActiveId } from './reviewLogic'
import {
  backStatus, NO_PLACES, placeFallback, placeKey, readPlaces, spotForMove, spotToOpen, storedPlaces, withSpot,
  type Places, type PlaceView, type Spot,
} from './reviewPlace'
import type { Restore } from './linesNavigation'
import type { LineTarget } from './reviewResults'

interface Live {
  dramaId: number
  activeId: number | null
  shown: ReviewLine[]
  filter: LineFilter
  searching: boolean
  term: string
  // The line being edited, if any.
  editing: number | null
  queue: boolean
  page: number
  listRef: RefObject<HTMLUListElement | null>
  // useLinesLoad's: the line a load made active by itself, which is not a move to keep.
  ownPickRef: MutableRefObject<number | null>
  goToLine: (t: LineTarget, restore?: Restore) => Promise<string | null>
  setStatus: Dispatch<SetStateAction<string | null>>
  setJumpedId: Dispatch<SetStateAction<number | null>>
}

interface Kept {
  // Set where a reopened row is found and spent when the row is drawn: the row is not in the DOM when its page lands.
  holdId: number | null
  held: { id: number; release: () => void } | null
  // The view is kept so that leaving it can take the sentence down.
  said: { text: string; view: PlaceView } | null
  live: Live | null
  // The `landed` of the restore still waiting for its page. The person's own moves share the pending slot, so this
  // tells them apart.
  restoreLanded: Pending['landed'] | null
  // Puts the page back when the person moves while a restore's page is loading: that page change is already made.
  undoMove: (() => void) | null
  // The line a restore moves to: its landing is not the person's move.
  restoreId: number | null
  // Bumped by what the person does, so a restore that awaits the whole title can tell. A page change counts, even one
  // that returns to the page it started on.
  acts: number
}

type KeptRef = MutableRefObject<Kept>

// Without it the row's top edge would touch the toolbar's bottom edge.
const TOOLBAR_GAP_PX = 8
type SetPage = Dispatch<SetStateAction<number>>

function readKept(dramaId: number): Places {
  return readPlaces(readPref<unknown>(browserStorage(), placeKey(dramaId), null))
}

function keep(dramaId: number, view: PlaceView, spot: Spot | null) {
  writePref(browserStorage(), placeKey(dramaId), storedPlaces(withSpot(readKept(dramaId), view, spot)))
}

// Landing on a kept line says so, unless it is the line the view would have opened on by itself: the status line, and
// the row's brief highlight, which stays with the row when the status line scrolls away with the top of a long list.
function sayBack(kept: KeptRef, view: PlaceView, line: ReviewLine, wouldOpen: number | null | undefined) {
  const live = kept.current.live
  const text = backStatus(line, wouldOpen)
  if (!live || !text) return
  kept.current.said = { text, view }
  live.setStatus(text)
  live.setJumpedId(line.id)
}

// The kept view on screen: the queue, All with no search, or neither (a filter or a search).
const shownView = (live: Live): PlaceView | null => (live.queue ? 'queue' : live.filter === 'all' && !live.searching ? 'lines' : null)

// The kept line is not on its page any more (lines were added, removed, merged or transcribed again before it).
// Reading the whole title takes a while on a long one, and the person may have started working meanwhile: then the
// restore gives up without a trace.
async function recover(kept: KeptRef, setPage: SetPage, spot: Spot, wouldOpen: number | null | undefined) {
  const start = kept.current.live
  if (!start) return
  const { filter, searching, page } = start
  const acts = kept.current.acts
  const stillHere = () => {
    const now = kept.current.live
    return !!now && now.filter === filter && now.searching === searching && now.page === page && kept.current.acts === acts
  }
  let all: ReviewLine[]
  try {
    all = await listAllLines(start.dramaId)
  } catch {
    // Unreadable now: the page stays as loaded, and the spot is kept for the next visit.
    return
  }
  if (!stillHere()) return
  const line = placeFallback(all, spot)
  if (!line) {
    keep(start.dramaId, 'lines', null)
    if (page !== 1) setPage(1)
    return
  }
  // The line may be on another page, so the sentence and the hold wait for the page to land on it.
  const landed: NonNullable<Pending['landed']> = (_lines, hit) => {
    kept.current.undoMove = null
    if (!hit) return
    kept.current.holdId = line.id
    kept.current.restoreId = line.id
    sayBack(kept, 'lines', line, wouldOpen)
  }
  kept.current.restoreLanded = landed
  kept.current.undoMove = () => setPage(page)
  const message = await kept.current.live?.goToLine({ lineId: line.id }, { all, stillHere, landed })
  if (message !== null) {
    kept.current.restoreLanded = null
    kept.current.undoMove = null
  }
}

// Opens a view's kept line on the page it was on, focused as a go-to does. `cur` and `first` are what the load would
// otherwise have picked from, so nothing is said when it is the line the view would have opened on anyway.
function reopen(kept: KeptRef, setPage: SetPage, view: PlaceView, spot: Spot, cur: number | null, first: boolean): Pending {
  const queue = view === 'queue'
  const move: Pending = {
    target: spot.lineId,
    landed: (lines, hit) => {
      const live = kept.current.live
      if (!live) return
      const wouldOpen = spot.page === 1 ? ownActiveId(lines, cur, { first, queue }) : undefined
      const line = hit ? lines.find((l) => l.id === spot.lineId) : undefined
      if (line) {
        // A queue card sits near the top and the focus brings it into view; a line further down All is held in view.
        if (!queue) kept.current.holdId = line.id
        kept.current.restoreId = line.id
        sayBack(kept, view, line, wouldOpen)
      } else if (!queue) {
        void recover(kept, setPage, spot, wouldOpen)
      } else {
        // Checked meanwhile: the queue opens on its first card, as it would have, and its spot is spent.
        keep(live.dramaId, 'queue', null)
        if (live.page !== 1) setPage(1)
      }
    },
  }
  kept.current.restoreLanded = move.landed
  kept.current.undoMove = null
  return move
}

// Keeps `row` where `place` puts it while the page settles: the player, the waveform and the side video arrive after
// the rows and change the toolbar's height or the layout. Stops at the person's first wheel, touch, key or press, or
// after 10 s.
function holdInView(row: HTMLElement, list: HTMLElement): () => void {
  const main = list.closest<HTMLElement>('.review-main')
  const section = list.closest<HTMLElement>('.review-editor')
  const toolbar = section?.querySelector<HTMLElement>('.review-toolbar') ?? null
  const place = () => {
    // The side-video layout scrolls the lines inside .review-main; otherwise the window scrolls.
    const own = main && getComputedStyle(main).overflowY !== 'visible' ? main : null
    const top = own ? own.getBoundingClientRect().top : 0
    const height = own ? own.clientHeight : window.innerHeight
    // A third of the way down, never under the sticky toolbar. Its stuck bottom is read from the toolbar itself: the
    // row's scroll margin can lag behind a toolbar the player and waveform have just made taller.
    const bar = toolbar ? getComputedStyle(toolbar) : null
    const stuck = toolbar && bar?.position === 'sticky' ? (parseFloat(bar.top) || 0) + toolbar.offsetHeight + TOOLBAR_GAP_PX : 0
    const want = Math.max(height / 3, parseFloat(getComputedStyle(row).scrollMarginTop) || 0, stuck)
    const off = row.getBoundingClientRect().top - top - want
    // Within a pixel is placed: a phone's collapsing toolbar could otherwise keep moving it.
    if (Math.abs(off) > 1) (own ?? window).scrollBy({ top: off, behavior: 'instant' })
  }
  place()
  const resized = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(place)
  if (toolbar) resized?.observe(toolbar)
  if (section) resized?.observe(section)
  const inputs = ['wheel', 'touchstart', 'keydown', 'pointerdown'] as const
  const stop = () => {
    resized?.disconnect()
    clearTimeout(timer)
    for (const e of inputs) window.removeEventListener(e, stop, true)
  }
  const timer = setTimeout(stop, 10_000)
  for (const e of inputs) window.addEventListener(e, stop, { capture: true, passive: true })
  return stop
}

/**
 * Where the list opens: its view (useInitialLinesFilter), and its page and pending move from the line kept for that
 * view, unless the link names lines (`focus`, `find`; `focus=source` only opens a panel). A go-to that arrives later, like any other move of the person's,
 * cancels a restore that is still on its way (`abandon`). Called before the list loads; useKeepPlace does the rest.
 */
export function useReviewPlace(dramaId: number, simple: boolean, pendingRef: MutableRefObject<Pending | null>) {
  const route = useRoute()
  const [filter, setFilter] = useState<LineFilter>(useInitialLinesFilter(simple))
  const [opening] = useState(() => {
    const link = route.name === 'drama' && ((route.focus !== undefined && route.focus !== 'source') || route.find !== undefined)
    return spotToOpen(readKept(dramaId), filter, simple, link)
  })
  const [page, setPage] = useState(opening?.spot.page ?? 1)
  const kept = useRef<Kept>({ holdId: null, held: null, said: null, live: null, restoreLanded: null, undoMove: null, restoreId: null, acts: 0 })
  // Runs before the list's first load, which is an effect of a later hook: that load settles on the kept line.
  useEffect(() => {
    if (opening) pendingRef.current = reopen(kept, setPage, opening.view, opening.spot, null, true)
  }, [opening, pendingRef])
  const place = {
    kept,
    /** The person has moved: a restore not yet landed is dropped, and so is a hold not yet placed. */
    abandon: () => {
      const k = kept.current
      k.acts += 1
      k.holdId = null
      k.restoreId = null
      if (pendingRef.current !== null && k.restoreLanded !== null && pendingRef.current.landed === k.restoreLanded) {
        pendingRef.current = null
        k.restoreLanded = null
        k.undoMove?.()
      }
      // Whatever replaced the restore's move (the pager, a save and next) made the page the person's.
      k.undoMove = null
    },
    /** The page a switch to `f` opens on: its view's kept line's, read now (with the move to it), else 1. */
    pageFor: (f: LineFilter): number => {
      // A restore still waiting is the old view's; only a move the person asked for (the main button, a jump) wins.
      place.abandon()
      const to = spotToOpen(kept.current.live ? readKept(kept.current.live.dramaId) : NO_PLACES, f, simple, pendingRef.current !== null)
      if (!to) return 1
      pendingRef.current = reopen(kept, setPage, to.view, to.spot, kept.current.live?.activeId ?? null, false)
      return to.spot.page
    },
  }
  return { filter, setFilter, page, setPage, place }
}

/** Keeps the line the person moves to, and holds a reopened row in view. Called after the list's own focus effect, which it follows. */
export function useKeepPlace(place: { kept: KeptRef; abandon: () => void }, live: Live) {
  const { kept } = place
  useEffect(() => {
    kept.current.live = live
  })

  useEffect(() => {
    const own = live.ownPickRef.current === live.activeId
    live.ownPickRef.current = null
    // The row left behind is no longer the one to keep in view.
    if (kept.current.held && kept.current.held.id !== live.activeId) {
      kept.current.held.release()
      kept.current.held = null
    }
    if (live.activeId === kept.current.restoreId) kept.current.restoreId = null
    else if (!own) place.abandon()
    const line = live.activeId !== null ? live.shown.find((l) => l.id === live.activeId) : undefined
    if (own || !line) return
    const { view, spot } = spotForMove(line, { queue: live.queue, all: live.filter === 'all' && !live.searching, page: live.page })
    keep(live.dramaId, view, spot)
    // Only a change of active line is a move; the rest is read as it is then.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live.activeId])

  // The sentence is about the view it was said in. Leaving that view takes it down, unless something else has been
  // said since: the status line is cleared only while it still reads that sentence.
  const shown = shownView(live)
  useEffect(() => {
    const said = kept.current.said
    if (!said || said.view === shown) return
    kept.current.said = null
    live.setStatus((now) => (now === said.text ? null : now))
    // Only a change of view takes it down.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shown])

  useEffect(() => {
    const id = kept.current.holdId
    if (id === null || id !== live.activeId) return
    const list = live.listRef.current
    const row = list?.querySelector<HTMLElement>(`[data-line-id="${id}"]`)
    if (!list || !row) return
    kept.current.holdId = null
    kept.current.held?.release()
    kept.current.held = { id, release: holdInView(row, list) }
  })
  // A restore that finishes after the list is gone must find nothing to act on.
  useEffect(() => () => {
    kept.current.held?.release()
    kept.current.live = null
  }, [kept])

  // A link that names a search can arrive while the list is open, outside the search box; it is the person's choice.
  // A term that empties is a change of view clearing the box, and that change's own restore must stand.
  const seenTerm = useRef(live.term)
  useEffect(() => {
    if (seenTerm.current === live.term) return
    seenTerm.current = live.term
    if (live.term !== '') place.abandon()
    // Only a change of term counts.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live.term])

  // Opening an edit on the line already active changes no active line, and it is as much the person's move.
  useEffect(() => {
    if (live.editing !== null) place.abandon()
    // Only an edit that opens, or moves to another line, counts.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live.editing])

  // Comparing pages cannot tell a page the person went to and came back from.
  useEffect(() => {
    kept.current.acts += 1
  }, [live.page, kept])
}
