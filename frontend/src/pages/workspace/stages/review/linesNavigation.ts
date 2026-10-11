import type { Dispatch, RefObject, SetStateAction } from 'react'

import { listAllLines } from '../../../../api/restructure'
import { idxFromLineNumber, lineNumber } from '../../../../lineNumber'
import type { LineFilter } from '../../../../types/review'
import type { createLinesController, LinesState, Pending } from './linesController'
import { pageForPosition } from './reviewLogic'
import type { LineTarget } from './reviewResults'

export interface LinesNavigationDeps {
  dramaId: number
  ctl: ReturnType<typeof createLinesController>
  st: { current: LinesState }
  pending: { current: Pending | null }
  listRef: RefObject<HTMLUListElement | null>
  searching: boolean
  filter: LineFilter
  page: number
  setFilter: Dispatch<SetStateAction<LineFilter>>
  setPage: Dispatch<SetStateAction<number>>
  setInput: Dispatch<SetStateAction<string>>
  setTerm: Dispatch<SetStateAction<string>>
  setStatus: Dispatch<SetStateAction<string | null>>
  setError: Dispatch<SetStateAction<unknown>>
}

// Filter, search and go-to-line: the moves that change which lines are shown.
// Each first saves (or keeps) a dirty draft. Rebuilt every render.
export function buildLinesNavigation(deps: LinesNavigationDeps) {
  const { dramaId, ctl, st, pending, listRef, searching, filter, page, setFilter, setPage, setInput, setTerm, setStatus, setError } = deps
const changeFilter = async (f: LineFilter) => {
  if (!(await ctl.leaveEdit())) return
  setFilter(f)
  setPage(1)
  setInput('')
  setTerm('')
}
const changeSearch = async (v: string) => {
  if (st.current.edit && !(await ctl.leaveEdit())) return
  setInput(v)
  if (v === '') setTerm('')
}
// By permanent id where the caller has one; typed numbers go by position.
// Returns null once the line is open, else a plain message saying why not.
const goToLine = async (t: LineTarget): Promise<string | null> => {
  const label = 'lineId' in t ? 'that line' : `#${t.lineNumber}`
  try {
    const all = await listAllLines(dramaId)
    const pos =
      'lineId' in t
        ? all.findIndex((l) => l.id === t.lineId)
        : all.findIndex((l) => l.idx === idxFromLineNumber(t.lineNumber))
    if (pos === -1) return 'lineId' in t ? 'That line no longer exists.' : `No line #${t.lineNumber}.`
    const draft = st.current.edit
    if (!(await ctl.leaveEdit())) {
      // The draft could not be saved: bring it into view so it can be fixed.
      if (draft) listRef.current?.querySelector<HTMLElement>(`[data-line-id="${draft.lineId}"]`)?.scrollIntoView?.({ block: 'center' })
      return `Could not open ${label}: your edit to #${draft ? lineNumber(draft.base.idx) : '?'} is not saved yet.`
    }
    const id = all[pos].id
    const pg = pageForPosition(pos)
    if (!searching && filter === 'all' && page === pg) ctl.focusTo(id)
    else {
      if (searching || filter !== 'all') setStatus(`Showing all lines to open #${lineNumber(all[pos].idx)}.`)
      pending.current = { target: id }
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
