import { useEffect, useRef, useState, type Dispatch, type MutableRefObject, type SetStateAction } from 'react'

import { listLines, searchLines } from '../../../../api/review'
import type { LineFilter, ReviewLine, ReviewLinesPage } from '../../../../types/review'
import type { EditState } from './LineRow'
import type { Pending, Target } from './linesController'
import { clashingFields, draftFromLine, rebaseDraft } from './reviewDraft'
import { initialActiveId, PAGE_SIZE } from './reviewLogic'

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
}

/** After Reload on a conflict: the kept draft moves onto the line as loaded.
 *  A field changed elsewhere shows that change (saving the typing over it
 *  unseen would lose it); the typing in every other field is kept. */
export const rebaseAfterReload = (lines: ReviewLine[]) => (cur: EditState | null): EditState | null => {
  if (!cur?.rebaseOnLoad) return cur
  const fresh = lines.find((l) => l.id === cur.lineId)
  if (!fresh) return { ...cur, rebaseOnLoad: false }
  const draft = rebaseDraft(cur.base, fresh, cur.draft)
  const now = draftFromLine(fresh)
  for (const k of clashingFields(cur.base, fresh, cur.draft)) (draft as unknown as Record<string, unknown>)[k] = now[k]
  return { ...cur, base: fresh, draft, rebaseOnLoad: false }
}

/** Save & next's edit on the loaded page, unless the user opened another
 *  edit while it loaded: that one is their latest choice. */
export const openPendingEdit = (target: ReviewLine) => (cur: EditState | null): EditState =>
  cur ?? { lineId: target.id, base: target, draft: draftFromLine(target), details: false, note: null }


// Loads the page (or the search result) on screen and settles which line is active afterwards:
// the one a pending move asked for, else the one already active, else the starting line.
export function useLinesLoad({ dramaId, page, filter, term, reloads, pending, focusActive, scrollActive, setActiveId, setEdit }: Deps) {
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
      setEdit(rebaseAfterReload(lines))
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
