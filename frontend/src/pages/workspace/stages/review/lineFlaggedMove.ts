import { flaggedAdjacent } from '../../../../api/review'
import type { StepKind } from '../../../../types/review'
import { flaggedStep, nextFlaggedId, nextNeedingSpeakerId, nextUnreviewedId, PAGE_SIZE } from './reviewLogic'
import type { LinesControllerDeps, LinesCore } from './linesController'

const WHAT: Record<StepKind, string> = { flagged: 'flagged lines', unreviewed: 'unchecked lines', no_speaker: 'lines with no speaker' }
const ONE: Record<StepKind, string> = { flagged: 'flagged line', unreviewed: 'unchecked line', no_speaker: 'line with no speaker' }
const NEXT_IN_RESULTS = { flagged: nextFlaggedId, unreviewed: nextUnreviewedId, no_speaker: nextNeedingSpeakerId }

export function createMoveFlagged(deps: LinesControllerDeps, core: LinesCore) {
  const { dramaId, st, pending, setStatus, setFilter, setPage, setError } = deps
  const { activate, goPage, leaveEdit } = core
  // Previous/next flagged line (Alt+Up/Down and the buttons): on this page
  // first, then the server finds the nearest one on any page (R08). In a
  // filtered view the server is asked from the focused row (flaggedStep).
  const moveFlagged = async (delta: 1 | -1, kind: StepKind = 'flagged') => {
    const what = WHAT[kind]
    const { shown: lines, page: pg, searching: s, filter: f, activeId: cur } = st.current
    const row = (document.activeElement as HTMLElement | null)?.closest?.('[data-line-id]')
    const from = row
      ? lines.findIndex((l) => String(l.id) === row.getAttribute('data-line-id'))
      : lines.findIndex((l) => l.id === cur)
    if (s) {
      const id = NEXT_IN_RESULTS[kind](lines, from, delta)
      if (id !== null) void activate(id, true)
      else setStatus(`No more ${what} in these results.`)
      return
    }
    const step = kind !== 'flagged' ? { fromId: from === -1 ? null : lines[from].id } : flaggedStep(lines, f, from, delta)
    if ('id' in step) {
      void activate(step.id, true)
      return
    }
    try {
      const r = await flaggedAdjacent(dramaId, delta > 0 ? 'next' : 'prev', step.fromId, PAGE_SIZE, f, undefined, kind)
      if (r.line_id === null || r.page_all === null) setStatus(`No more ${what}.`)
      else if (r.page === pg) void activate(r.line_id, true)
      else if (r.page !== null) void goPage(r.page, r.line_id)
      else {
        // The filter hides it: show every line to open it.
        if (!(await leaveEdit())) return
        pending.current = { target: r.line_id }
        setStatus(`Showing all lines to open the ${ONE[kind]}.`)
        setFilter('all')
        setPage(r.page_all)
      }
    } catch (e) {
      setError(e)
    }
  }
  return moveFlagged
}
