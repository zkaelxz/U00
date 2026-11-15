import type { Dispatch, RefObject, SetStateAction } from 'react'

import { listAllLines } from '../../../../api/restructure'
import { idxFromLineNumber, lineNumber } from '../../../../lineNumber'
import type { LineFilter, ReviewLine } from '../../../../types/review'
import type { createLinesController, LinesState, Pending } from './linesController'
import { pageForPosition } from './reviewLogic'
import type { LineTarget } from './reviewResults'

interface LinesNavigationDeps {
  dramaId: number
  ctl: ReturnType<typeof createLinesController>
  st: { current: LinesState }
  pending: { current: Pending | null }
  listRef: RefObject<HTMLUListElement | null>
  searching: boolean
  filter: LineFilter
  page: number
  // The kept places (useReviewPlace): the page a view opens on when the person switches to it, and the person's moves
  // that cancel a restore still on its way.
  place: { pageFor: (f: LineFilter) => number; abandon: () => void }
  setFilter: Dispatch<SetStateAction<LineFilter>>
  setPage: Dispatch<SetStateAction<number>>
  setInput: Dispatch<SetStateAction<string>>
  setTerm: Dispatch<SetStateAction<string>>
  setStatus: Dispatch<SetStateAction<string | null>>
  setError: Dispatch<SetStateAction<unknown>>
}

// A restore of the kept place goes through goToLine with the title it has already read, and stops if the person moved.
export interface Restore {
  all: ReviewLine[]
  stillHere: () => boolean
  // Told once the page has landed on the line. It is also the pending move's `landed`, which is how the kept place
  // recognises that move as the restore's and drops it when the person moves first.
  landed: NonNullable<Pending['landed']>
}

// goToLine's answer is for the person to read; a restore that stops has nothing to tell them.
export const RESTORE_STOPPED = ''

// Filter, search and go-to-line: the moves that change which lines are shown.
// Each first saves (or keeps) a dirty draft. Rebuilt every render.
export function buildLinesNavigation(deps: LinesNavigationDeps) {
  const { dramaId, ctl, st, pending, listRef, searching, filter, page, place, setFilter, setPage, setInput, setTerm, setStatus, setError } = deps
// Whether the view changed: false when a draft that cannot be saved holds the person where they are.
const changeFilter = async (f: LineFilter): Promise<boolean> => {
  if (!(await ctl.leaveEdit())) return false
  setFilter(f)
  setPage(place.pageFor(f))
  setInput('')
  setTerm('')
  return true
}
const changeSearch = async (v: string) => {
  place.abandon()
  // The box takes the keystroke at once: waiting for the save first dropped
  // typed characters. A draft that can't be saved stays in the edit banner,
  // and isn't sent again on every keystroke.
  setInput(v)
  if (v === '') setTerm('')
  const cur = st.current.edit
  if (cur && !ctl.saveFailed(cur.lineId)) await ctl.leaveEdit()
}
// By permanent id where the caller has one; typed numbers go by position.
// Returns null once the line is open, else a plain message saying why not.
const goToLine = async (t: LineTarget, restore?: Restore): Promise<string | null> => {
  const label = 'lineId' in t ? 'that line' : `#${t.lineNumber}`
  // A go-to is the person's choice from the moment it is asked, even if the title is still being read.
  if (!restore) place.abandon()
  try {
    const all = restore?.all ?? (await listAllLines(dramaId))
    const pos =
      'lineId' in t
        ? all.findIndex((l) => l.id === t.lineId)
        : all.findIndex((l) => l.idx === idxFromLineNumber(t.lineNumber))
    if (pos === -1) return 'lineId' in t ? 'That line no longer exists.' : `No line #${t.lineNumber}.`
    // Leaving an edit saves it, which a restore must never do on the person's behalf.
    if (restore && st.current.edit) return RESTORE_STOPPED
    const draft = st.current.edit
    if (!(await ctl.leaveEdit())) {
      // The draft could not be saved: bring it into view so it can be fixed.
      if (draft) listRef.current?.querySelector<HTMLElement>(`[data-line-id="${draft.lineId}"]`)?.scrollIntoView?.({ block: 'center' })
      return `Could not open ${label}: your edit to #${draft ? lineNumber(draft.base.idx) : '?'} is not saved yet.`
    }
    if (restore && !restore.stillHere()) return RESTORE_STOPPED
    const id = all[pos].id
    const pg = pageForPosition(pos)
    if (!searching && filter === 'all' && page === pg) {
      ctl.focusTo(id)
      restore?.landed(st.current.shown, true)
    } else {
      if (searching || filter !== 'all') setStatus(`Showing all lines to open #${lineNumber(all[pos].idx)}.`)
      pending.current = { target: id, landed: restore?.landed }
      setFilter('all')
      setInput('')
      setTerm('')
      setPage(pg)
    }
    return null
  } catch (e) {
    setError(e)
    return `Could not open ${label}.`
  }
}
const goToNumber = async (n: number) => {
  const message = await goToLine({ lineNumber: n })
  if (message) setStatus(message)
}
  return { changeFilter, changeSearch, goToLine, goToNumber }
}
