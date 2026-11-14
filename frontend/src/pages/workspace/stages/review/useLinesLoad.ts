import { useEffect, useRef, useState, type Dispatch, type MutableRefObject, type SetStateAction } from 'react'

import { listLines, searchLines } from '../../../../api/review'
import type { LineFilter, ReviewLine, ReviewLinesPage } from '../../../../types/review'
import { QUEUE_FILTER } from './checkQueueModel'
import type { EditState } from './LineRow'
import { linesLoaded, type Pending, type Target } from './linesController'
import { draftFromLine } from './reviewDraft'
import { initialActiveId, PAGE_SIZE, pageCount } from './reviewLogic'

function pick(lines: ReviewLine[], t: Target): ReviewLine | undefined {
  if (t === 'first') return lines[0]
  if (t === 'last') return lines[lines.length - 1]
  return lines.find((l) => l.id === t)
}

interface Deps {
  dramaId: number
  page: number
  filter: LineFilter
  term: string
  reloads: number
  pending: MutableRefObject<Pending | null>
  focusActive: MutableRefObject<number | null>
  setActiveId: Dispatch<SetStateAction<number | null>>
  setEdit: Dispatch<SetStateAction<EditState | null>>
  setPage: Dispatch<SetStateAction<number>>
}

/** Save & next's edit on the loaded page, unless the user opened another
 *  edit while it loaded: that one is their latest choice. */
export const openPendingEdit = (target: ReviewLine) => (cur: EditState | null): EditState =>
  cur ?? { lineId: target.id, base: target, draft: draftFromLine(target), details: false, note: null }


// Loads the page (or the search result) on screen and settles which line is active afterwards:
// the one a pending move asked for, else the one already active, else the starting line.
export function useLinesLoad({ dramaId, page, filter, term, reloads, pending, focusActive, setActiveId, setEdit, setPage }: Deps) {
  const [data, setData] = useState<ReviewLinesPage | null>(null)
  const [found, setFound] = useState<ReviewLine[] | null>(null)
  const [allTotal, setAllTotal] = useState<number | null>(null)
  // What `data` was loaded for. A view or a reload asked for since then has not arrived, and until it does the
  // page on screen is the earlier one's: the check queue must not judge "Mark the rest" from it.
  const [loadedFor, setLoadedFor] = useState<{ filter: LineFilter; reloads: number } | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [retries, setRetries] = useState(0)
  const loadedOnce = useRef(false)

  useEffect(() => {
    let cancelled = false
    const settle = (lines: ReviewLine[]) => {
      const p = pending.current
      pending.current = null
      const target = p ? pick(lines, p.target) : undefined
      // An edit kept by Reload moves onto the line as loaded.
      linesLoaded(dramaId, lines)
      if (target) {
        setActiveId(target.id)
        focusActive.current = target.id
        // An edit opened while this page loaded is the user's latest choice: keep it.
        if (p?.edit) setEdit(openPendingEdit(target))
      } else {
        const first = !loadedOnce.current
        setActiveId((cur) =>
          cur !== null && lines.some((l) => l.id === cur)
            ? cur
            : first
              // The check queue lists the riskiest line first, flagged or not, so it opens on its first card.
              ? (!term && filter === QUEUE_FILTER ? (lines[0]?.id ?? null) : initialActiveId(lines))
              : (lines[0]?.id ?? null),
        )
      }
      loadedOnce.current = true
    }
    const req = term
      ? searchLines(dramaId, term).then((r) => {
          if (cancelled) return
          setFound(r)
          settle(r)
        })
      : listLines(dramaId, page, PAGE_SIZE, filter).then((r) => {
          if (cancelled) return
          // A write that shrinks the title can leave the reviewer past the last page, which would read as an empty title.
          if (r.lines.length === 0 && r.total > 0 && page > pageCount(r.total)) return setPage(pageCount(r.total))
          setFound(null)
          setData(r)
          setLoadedFor({ filter, reloads })
          if (filter === 'all') setAllTotal(r.total)
          settle(r.lines)
        })
    req.then(
      () => !cancelled && setError(null),
      (e: unknown) => !cancelled && setError(e),
    )
    return () => {
      cancelled = true
    }
    // pending, the focus refs and the setters are stable; only these inputs reload.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dramaId, page, filter, term, reloads, retries])

  // A filtered view's answer counts only its own lines, so the whole title's count is read beside it: the All
  // switch, "N of M look fine" and the stage's has-lines check need it even when Review opens on a filter.
  useEffect(() => {
    if (filter === 'all') return
    let cancelled = false
    listLines(dramaId, 1, 1, 'all').then((r) => !cancelled && setAllTotal(r.total), () => undefined)
    return () => {
      cancelled = true
    }
  }, [dramaId, filter, reloads])

  const retry = () => {
    setError(null)
    setRetries((n) => n + 1)
  }
  return {
    data, setData, found, setFound, allTotal, error, setError, retry,
    dataFilter: loadedFor?.filter ?? null,
    settled: loadedFor?.filter === filter && loadedFor.reloads === reloads,
  }
}
