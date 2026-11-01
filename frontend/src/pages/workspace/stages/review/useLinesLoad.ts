import { useEffect, useRef, useState, type Dispatch, type MutableRefObject, type SetStateAction } from 'react'

import { listLines, searchLines } from '../../../../api/review'
import type { LineFilter, ReviewLine, ReviewLinesPage } from '../../../../types/review'
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
  scrollActive: MutableRefObject<boolean>
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
export function useLinesLoad({ dramaId, page, filter, term, reloads, pending, focusActive, scrollActive, setActiveId, setEdit, setPage }: Deps) {
  const [data, setData] = useState<ReviewLinesPage | null>(null)
  const [found, setFound] = useState<ReviewLine[] | null>(null)
  const [allTotal, setAllTotal] = useState<number | null>(null)
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
        // On load, bring the starting line into view (without taking focus).
        if (first) scrollActive.current = true
        setActiveId((cur) =>
          cur !== null && lines.some((l) => l.id === cur)
            ? cur
            : first
              ? initialActiveId(lines)
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

  const retry = () => {
    setError(null)
    setRetries((n) => n + 1)
  }
  return { data, setData, found, setFound, allTotal, error, setError, retry }
}
