/** The kept place in Review's line list: the view, page and line it opens on for a title, the sentence that says so, and keeping the line the person moves to. */
import { useEffect, useRef, useState, type Dispatch, type MutableRefObject, type RefObject, type SetStateAction } from 'react'

import { listAllLines } from '../../../../api/restructure'
import { browserStorage, PREF_KEY_PREFIX, readPref, writePref } from '../../../../hooks/usePersistedState'
import { useRoute } from '../../../../router'
import type { LineFilter, ReviewLine } from '../../../../types/review'
import type { Pending } from './linesController'
import { useInitialLinesFilter } from './useCheckQueue'
import { ownActiveId } from './reviewLogic'
import { backStatus, openingPlace, placeFallback, placeForMove, placeKey, readPlace, type ReviewPlace } from './reviewPlace'
import type { LineTarget } from './reviewResults'

interface Live {
  dramaId: number
  activeId: number | null
  shown: ReviewLine[]
  // The first page (or search) has arrived.
  loaded: boolean
  filter: LineFilter
  searching: boolean
  // The check queue is showing.
  queue: boolean
  page: number
  listRef: RefObject<HTMLUListElement | null>
  goToLine: (t: LineTarget) => Promise<string | null>
  setStatus: Dispatch<SetStateAction<string | null>>
}

interface Kept {
  // A place kept in the lines while the list opened on the queue: All opens on it the first time it is picked.
  onAll: ReviewPlace | null
  // The reopened row, scrolled into place once it is drawn.
  scrollTo: number | null
  // The next active line is one the page moved to by itself (the queue's first card, or the start as before), not a move to keep.
  quiet: boolean
  live: Live | null
}

type KeptRef = MutableRefObject<Kept>
type SetPage = Dispatch<SetStateAction<number>>

function forget(dramaId: number) {
  try {
    browserStorage()?.removeItem(PREF_KEY_PREFIX + placeKey(dramaId))
  } catch {
    // Blocked storage keeps nothing to forget.
  }
}

// The kept line is not on its page any more (lines were added, removed, merged or transcribed again before it).
async function recover(kept: KeptRef, setPage: SetPage, place: ReviewPlace, wouldOpen: number | null | undefined) {
  const live = kept.current.live
  if (!live) return
  let all: ReviewLine[]
  try {
    all = await listAllLines(live.dramaId)
  } catch {
    // Unreadable now: the page stays as loaded, and the place is kept for the next visit.
    return
  }
  const line = placeFallback(all, place)
  if (!line) {
    forget(live.dramaId)
    if (kept.current.live && kept.current.live.page !== 1) {
      kept.current.quiet = true
      setPage(1)
    }
    return
  }
  kept.current.scrollTo = line.id
  const message = await kept.current.live?.goToLine({ lineId: line.id })
  if (message !== null) {
    kept.current.scrollTo = null
    return
  }
  const said = backStatus(line, wouldOpen)
  if (said) kept.current.live?.setStatus(said)
}

// Opens the kept line on the page it was on, focused as a go-to does. `cur` and `first` are what the load would
// otherwise have picked from, so the status says nothing when it is the line the list would have opened on anyway.
function reopen(kept: KeptRef, setPage: SetPage, place: ReviewPlace, cur: number | null, first: boolean): Pending {
  const queue = place.view === 'queue'
  return {
    target: place.lineId,
    landed: (lines, hit) => {
      const live = kept.current.live
      if (!live) return
      const wouldOpen = place.page === 1 ? ownActiveId(lines, cur, { first, queue }) : undefined
      const line = hit ? lines.find((l) => l.id === place.lineId) : undefined
      if (line) {
        // The queue's cards sit near the top; a line further down All is brought clear of the bottom edge.
        if (!queue) kept.current.scrollTo = line.id
        const said = backStatus(line, wouldOpen)
        if (said) live.setStatus(said)
      } else if (!queue) {
        void recover(kept, setPage, place, wouldOpen)
      } else if (live.page !== 1) {
        // Checked meanwhile: the queue opens on its first card, as it would have.
        kept.current.quiet = true
        setPage(1)
      }
    },
  }
}

/**
 * Where the list opens: its view (useInitialLinesFilter), and its page and pending move from the place kept for this
 * title, unless the link names lines (`focus`, `find`) or a go-to is already asked. Called before the list loads;
 * useKeepPlace does the rest.
 */
export function useReviewPlace(dramaId: number, simple: boolean, goToAsked: boolean, pendingRef: MutableRefObject<Pending | null>) {
  const route = useRoute()
  const [filter, setFilter] = useState<LineFilter>(useInitialLinesFilter(simple))
  const [opening] = useState(() => {
    const link = goToAsked || (route.name === 'drama' && (route.focus !== undefined || route.find !== undefined))
    return openingPlace(readPlace(readPref<unknown>(browserStorage(), placeKey(dramaId), null)), filter, link)
  })
  const [page, setPage] = useState(opening.now?.page ?? 1)
  const kept = useRef<Kept>({ onAll: opening.onAll, scrollTo: null, quiet: false, live: null })
  // Runs before the list's first load, which is an effect of a later hook: that load settles on the kept line.
  useEffect(() => {
    if (opening.now) pendingRef.current = reopen(kept, setPage, opening.now, null, true)
  }, [opening, pendingRef])
  const place = {
    kept,
    /** The page All opens on when picked: the kept line's the first time this visit (with the move to it), else 1. */
    toAll: (): number => {
      const at = kept.current.onAll
      kept.current.onAll = null
      if (!at || pendingRef.current) return 1
      pendingRef.current = reopen(kept, setPage, at, kept.current.live?.activeId ?? null, false)
      return at.page
    },
  }
  return { filter, setFilter, page, setPage, place }
}

/** Keeps the line the person moves to, and scrolls a reopened row into place. Called after the list's own focus effect, which it follows. */
export function useKeepPlace(place: { kept: KeptRef }, live: Live) {
  const { kept } = place
  useEffect(() => {
    kept.current.live = live
  })
  // Any way into All (a go-to too) spends the wait for it.
  useEffect(() => {
    if (live.filter === 'all') kept.current.onAll = null
  }, [kept, live.filter])

  // The first loaded active line is the page's own pick (or the kept line itself): only later moves are kept.
  const firstLoad = useRef(true)
  useEffect(() => {
    if (!live.loaded) return
    const own = firstLoad.current || kept.current.quiet
    firstLoad.current = false
    kept.current.quiet = false
    const line = live.activeId !== null ? live.shown.find((l) => l.id === live.activeId) : undefined
    if (!line) return
    const at = placeForMove(line, { ownPick: own, queue: live.queue, all: live.filter === 'all' && !live.searching, page: live.page })
    if (at) writePref(browserStorage(), placeKey(live.dramaId), at)
    // Only a change of active line is a move; the rest is read as it is then.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live.activeId, live.loaded])

  useEffect(() => {
    const id = kept.current.scrollTo
    if (id === null || id !== live.activeId) return
    const row = live.listRef.current?.querySelector<HTMLElement>(`[data-line-id="${id}"]`)
    if (!row) return
    kept.current.scrollTo = null
    // About a third down the window, and never under the sticky toolbar, whose height the row's scroll margin follows.
    const room = Math.max(window.innerHeight / 3, parseFloat(getComputedStyle(row).scrollMarginTop) || 0)
    window.scrollBy({ top: row.getBoundingClientRect().top - room })
  })
}
