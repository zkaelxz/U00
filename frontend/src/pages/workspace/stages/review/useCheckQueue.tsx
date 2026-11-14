/** The check queue's part of the Review line list: the view it opens on, the rows it shows, and the switch, footer and Undo around them. */
import { useEffect, useMemo, useRef, useState, type MutableRefObject, type ReactNode } from 'react'

import { setReviewed } from '../../../../api/review'
import type { LineFilter, ReviewLine, ReviewLinesPage } from '../../../../types/review'
import { useRoute } from '../../../../router'
import { useCheckSignal } from '../../StageContext'
import { CheckedUndo, CheckQueueFooter, CheckQueueSwitch } from './CheckQueue'
import { initialLinesFilter, nextText, positionText, QUEUE_FILTER, queueCounts } from './checkQueueModel'
import type { LinesController, LinesState, Pending } from './linesController'
import { PAGE_SIZE } from './reviewLogic'
import { JOB_RUNNING_MESSAGE } from './reviewResegment'
import { retranscribeMenuState } from './retranscribeLogic'
import { useUndoOffer } from './undoOffer'

/** The view the line list opens on: the check queue in Simple, unless the link names lines (initialLinesFilter). */
export function useInitialLinesFilter(simple: boolean): LineFilter {
  const route = useRoute()
  return initialLinesFilter(route.name === 'drama' ? route : null, simple)
}

interface Deps {
  dramaId: number
  simple: boolean
  filter: LineFilter
  searching: boolean
  isPhone: boolean
  jobRunning: boolean
  canRetranscribeLine: boolean
  retranscribeNeed: { reason: string } | null
  data: ReviewLinesPage | null
  // The view `data` was loaded for, and whether it is the answer to the latest view and reload asked for.
  dataFilter: LineFilter | null
  settled: boolean
  allTotal: number | null
  shown: ReviewLine[]
  activeId: number | null
  page: number
  pages: number
  ctl: LinesController
  pending: MutableRefObject<Pending | null>
  st: MutableRefObject<LinesState>
  changeFilter: (f: LineFilter) => Promise<void>
  onChanged: () => void
  setError: (e: unknown) => void
}

export function useCheckQueue(d: Deps) {
  const { dramaId, ctl, pending, st } = d
  // A search shows its own results, so the queue is off while one is typed.
  const on = d.simple && d.filter === QUEUE_FILTER && !d.searching
  const [checked, setChecked] = useUndoOffer<ReviewLine>('checked', dramaId)
  const [undoing, setUndoing] = useState(false)

  // Simple's Check button, pressed on this page: open the queue on its first card.
  const signal = useCheckSignal()
  const seenSignal = useRef(signal)
  const openRef = useRef<() => Promise<void>>(async () => {})
  useEffect(() => {
    openRef.current = async () => {
      const { filter, searching, page, shown } = st.current
      if (filter === QUEUE_FILTER && !searching && page === 1) {
        // A phone draws only the open card, so a draft on another card is saved before the first card takes its place.
        if (shown[0] && (await ctl.leaveEdit())) ctl.focusTo(shown[0].id)
        return
      }
      if (!(await ctl.leaveEdit())) return
      pending.current = { target: 'first' }
      await d.changeFilter(QUEUE_FILTER)
    }
  })
  useEffect(() => {
    if (signal === seenSignal.current) return
    seenSignal.current = signal
    void openRef.current()
  }, [signal])

  const undo = async () => {
    if (!checked || !(await ctl.leaveEdit())) return
    setUndoing(true)
    setReviewed(dramaId, checked.id, false, checked.en)
      .then(() => {
        // Unchecked, the line is back in the queue unless an edit of its English cleared its flag: open it there.
        pending.current = { target: checked.id }
        setChecked(null)
        d.onChanged()
      }, d.setError)
      .finally(() => setUndoing(false))
  }

  const needReason = d.retranscribeNeed?.reason ?? null
  // The line menu's own gating for Re-transcribe…, kept stable so a card only redraws when it changes.
  const retranscribe = useMemo(
    () => (on ? retranscribeMenuState(d.canRetranscribeLine, d.jobRunning ? JOB_RUNNING_MESSAGE : null, needReason) : null),
    [on, d.canRetranscribeLine, d.jobRunning, needReason],
  )
  const counts = d.data ? queueCounts(d.data, d.allTotal) : null
  const cardOnly = on && d.isPhone
  const position = cardOnly && d.data ? positionText(d.shown, d.activeId, d.page, PAGE_SIZE, d.data.total) : null
  const upNext = cardOnly ? nextText(d.shown, d.activeId) : null
  // Skip for now: the next card, the next page's first, or back to the start of the queue.
  const skip = () => {
    const at = d.shown.findIndex((l) => l.id === d.activeId)
    if (at < d.shown.length - 1 || d.page < d.pages) ctl.move(1)
    else if (d.page > 1) void ctl.goPage(1, 'first')
    else if (d.shown[0]) ctl.actions.activate(d.shown[0].id)
  }

  const lead: ReactNode = d.simple ? (
    <>
      {/* The phone's card view leaves "F of M look fine" to the footer's "The other F lines…", so the switch row stays one line. */}
      <CheckQueueSwitch queue={on} counts={counts} showFine={!(cardOnly && (counts?.risk ?? 0) > 0)} onPick={(q) => void d.changeFilter(q ? QUEUE_FILTER : 'all')} />
      {checked && <CheckedUndo line={checked} busy={undoing} onUndo={() => void undo()} onDismiss={() => setChecked(null)} />}
      {position && <p className="check-queue-position" data-testid="queue-position">{position}</p>}
    </>
  ) : undefined
  // Just after the switch is pressed, `data` is still the page of the view before: the footer waits for the queue's own.
  const after: ReactNode = on && d.data && counts && d.dataFilter === QUEUE_FILTER ? (
    <>
      {upNext && <p className="muted check-queue-next">{upNext}</p>}
      <CheckQueueFooter
        dramaId={dramaId}
        queuePage={d.data}
        counts={counts}
        jobRunning={d.jobRunning}
        settled={d.settled}
        onReadAll={() => void d.changeFilter('all')}
        onUntranslated={() => void d.changeFilter('untranslated')}
        onChanged={d.onChanged}
      />
    </>
  ) : null

  return {
    on,
    // A phone shows one card at a time.
    rows: cardOnly ? d.shown.filter((l) => l.id === d.activeId) : d.shown,
    toolbar: { lead, bare: on },
    retranscribe,
    after,
    bar: (line: ReviewLine) => (on ? { onSkip: skip, onLooksRight: () => ctl.actions.reviewAndNext(line.id) } : undefined),
  }
}
