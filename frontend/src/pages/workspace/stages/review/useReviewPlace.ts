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
import type { LineTarget } from './reviewResults'

interface Live {
  dramaId: number
  activeId: number | null
  shown: ReviewLine[]
  filter: LineFilter
  searching: boolean
  // The check queue is showing.
  queue: boolean
  page: number
  listRef: RefObject<HTMLUListElement | null>
  // useLinesLoad's: the line a load made active by itself, which is not a move to keep.
  ownPickRef: MutableRefObject<number | null>
  goToLine: (t: LineTarget) => Promise<string | null>
  setStatus: Dispatch<SetStateAction<string | null>>
  setJumpedId: Dispatch<SetStateAction<number | null>>
}

interface Kept {
  // The reopened row, to hold in view once it is drawn.
  holdId: number | null
  // The row being held, and what stops holding it.
  held: { id: number; release: () => void } | null
  live: Live | null
}

type KeptRef = MutableRefObject<Kept>

// Room left between the sticky toolbar and a reopened row.
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
function sayBack(live: Live, line: ReviewLine, wouldOpen: number | null | undefined) {
  const said = backStatus(line, wouldOpen)
  if (!said) return
  live.setStatus(said)
  live.setJumpedId(line.id)
}

// The kept line is not on its page any more (lines were added, removed, merged or transcribed again before it).
async function recover(kept: KeptRef, setPage: SetPage, spot: Spot, wouldOpen: number | null | undefined) {
  const live = kept.current.live
  if (!live) return
  let all: ReviewLine[]
  try {
    all = await listAllLines(live.dramaId)
  } catch {
    // Unreadable now: the page stays as loaded, and the spot is kept for the next visit.
    return
  }
  const line = placeFallback(all, spot)
  if (!line) {
    keep(live.dramaId, 'lines', null)
    if (kept.current.live && kept.current.live.page !== 1) setPage(1)
    return
  }
  kept.current.holdId = line.id
  const message = await kept.current.live?.goToLine({ lineId: line.id })
  if (message !== null) {
    kept.current.holdId = null
    return
  }
  if (kept.current.live) sayBack(kept.current.live, line, wouldOpen)
}

// Opens a view's kept line on the page it was on, focused as a go-to does. `cur` and `first` are what the load would
// otherwise have picked from, so nothing is said when it is the line the view would have opened on anyway.
function reopen(kept: KeptRef, setPage: SetPage, view: PlaceView, spot: Spot, cur: number | null, first: boolean): Pending {
  const queue = view === 'queue'
  return {
    target: spot.lineId,
    landed: (lines, hit) => {
      const live = kept.current.live
      if (!live) return
      const wouldOpen = spot.page === 1 ? ownActiveId(lines, cur, { first, queue }) : undefined
      const line = hit ? lines.find((l) => l.id === spot.lineId) : undefined
      if (line) {
        // A queue card sits near the top and the focus brings it into view; a line further down All is held in view.
        if (!queue) kept.current.holdId = line.id
        sayBack(live, line, wouldOpen)
      } else if (!queue) {
        void recover(kept, setPage, spot, wouldOpen)
      } else {
        // Checked meanwhile: the queue opens on its first card, as it would have, and its spot is spent.
        keep(live.dramaId, 'queue', null)
        if (live.page !== 1) setPage(1)
      }
    },
  }
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
 * view, unless the link names lines (`focus`, `find`) or a go-to is already asked. Called before the list loads;
 * useKeepPlace does the rest.
 */
export function useReviewPlace(dramaId: number, simple: boolean, goToAsked: boolean, pendingRef: MutableRefObject<Pending | null>) {
  const route = useRoute()
  const [filter, setFilter] = useState<LineFilter>(useInitialLinesFilter(simple))
  const [opening] = useState(() => {
    const link = goToAsked || (route.name === 'drama' && (route.focus !== undefined || route.find !== undefined))
    return spotToOpen(readKept(dramaId), filter, simple, link)
  })
  const [page, setPage] = useState(opening?.spot.page ?? 1)
  const kept = useRef<Kept>({ holdId: null, held: null, live: null })
  // Runs before the list's first load, which is an effect of a later hook: that load settles on the kept line.
  useEffect(() => {
    if (opening) pendingRef.current = reopen(kept, setPage, opening.view, opening.spot, null, true)
  }, [opening, pendingRef])
  const place = {
    kept,
    /** The page a switch to `f` opens on: its view's kept line's, read now (with the move to it), else 1. */
    pageFor: (f: LineFilter): number => {
      const to = spotToOpen(kept.current.live ? readKept(kept.current.live.dramaId) : NO_PLACES, f, simple, pendingRef.current !== null)
      if (!to) return 1
      pendingRef.current = reopen(kept, setPage, to.view, to.spot, kept.current.live?.activeId ?? null, false)
      return to.spot.page
    },
  }
  return { filter, setFilter, page, setPage, place }
}

/** Keeps the line the person moves to, and holds a reopened row in view. Called after the list's own focus effect, which it follows. */
export function useKeepPlace(place: { kept: KeptRef }, live: Live) {
  const { kept } = place
  useEffect(() => {
    kept.current.live = live
  })

  useEffect(() => {
    const own = live.ownPickRef.current === live.activeId
    live.ownPickRef.current = null
    // A move elsewhere ends the hold on a reopened row.
    if (kept.current.held && kept.current.held.id !== live.activeId) {
      kept.current.held.release()
      kept.current.held = null
    }
    const line = live.activeId !== null ? live.shown.find((l) => l.id === live.activeId) : undefined
    if (own || !line) return
    const { view, spot } = spotForMove(line, { queue: live.queue, all: live.filter === 'all' && !live.searching, page: live.page })
    keep(live.dramaId, view, spot)
    // Only a change of active line is a move; the rest is read as it is then.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live.activeId])

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
  useEffect(() => () => kept.current.held?.release(), [kept])
}
