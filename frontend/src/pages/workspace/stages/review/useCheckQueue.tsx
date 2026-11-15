/** The check queue's part of the Review line list: the view it opens on, the rows it shows, and the switch, footer and Undo around them. */
import { useCallback, useEffect, useMemo, useRef, useState, type MutableRefObject, type ReactNode, type RefObject } from 'react'
import { createPortal } from 'react-dom'

import { setReviewed } from '../../../../api/review'
import type { LineFilter, ReviewLine, ReviewLinesPage } from '../../../../types/review'
import { useRoute } from '../../../../router'
import { useCheckLead, useCheckSignal, useLinesOnAll, usePlayerDock, usePositionReport, useSearchRequest } from '../../StageContext'
import { useMediaQuery } from '../../../../hooks/useMediaQuery'
import { usePersistedState } from '../../../../hooks/usePersistedState'
import { buttonClass } from '../../../../components/uiClasses'
import { CheckedUndo, CheckLeadAction, CheckQueueFooter, CheckQueueSwitch, QueueFine, QueueMediaToggle } from './CheckQueue'
import { checkActionLabel } from '../../stepModel'
import { initialLinesFilter, nextText, QUEUE_FILTER, queueCounts, queuePlace } from './checkQueueModel'
import type { LinesController, LinesState, Pending } from './linesController'
import { PAGE_SIZE } from './reviewLogic'
import { JOB_RUNNING_MESSAGE } from './reviewResegment'
import { retranscribeMenuState } from './retranscribeLogic'
import { TOOLS_BESIDE_LINES } from './reviewToolsTabs'
import { useUndoOffer } from './undoOffer'

/** The view the line list opens on: the check queue in Simple, unless the link names lines (initialLinesFilter). */
export function useInitialLinesFilter(simple: boolean): LineFilter {
  const route = useRoute()
  const onAll = useLinesOnAll()
  return initialLinesFilter(route.name === 'drama' ? route : null, simple, onAll)
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
  // The error of the latest load; a new one means the view asked for is not coming.
  loadError: unknown
  // The first page of lines is on its way.
  loading: boolean
  allTotal: number | null
  shown: ReviewLine[]
  activeId: number | null
  page: number
  pages: number
  ctl: LinesController
  pending: MutableRefObject<Pending | null>
  listRef: RefObject<HTMLUListElement | null>
  st: MutableRefObject<LinesState>
  changeFilter: (f: LineFilter) => Promise<boolean>
  onChanged: () => void
  setError: (e: unknown) => void
  // The Player toggle of an audio title: drawn in the header's More row (see useQueueMedia), so it is only placed here.
  mediaToggle?: ReactNode
}

export function useCheckQueue(d: Deps) {
  const { dramaId, ctl, pending, st } = d
  // A search shows its own results, so the queue is off while one is typed.
  const on = d.simple && d.filter === QUEUE_FILTER && !d.searching
  const [checked, setChecked] = useUndoOffer<ReviewLine>('checked', dramaId)
  const [undoing, setUndoing] = useState(false)

  // Simple's Check button, pressed on this page: open the queue on its first card.
  const signal = useCheckSignal()
  const checkLead = useCheckLead()
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

  // The header's Search: All, then the search box with the focus. A search typed already stays, so All is asked for only
  // when the lines are not on it. From another step's page the request is waiting when the lines mount.
  const { searchSignal = 0, takeSearch } = useSearchRequest()
  const seenSearch = useRef(searchSignal)
  const [searchAsk, setSearchAsk] = useState(0)
  const searchRef = useRef<() => Promise<void>>(async () => {})
  useEffect(() => {
    searchRef.current = async () => {
      const { filter, searching } = st.current
      // An ask left unanswered would take the focus whenever the search box is next drawn, so it is raised only once the lines are on All.
      if (!searching && filter !== 'all' && !(await d.changeFilter('all'))) return
      setSearchAsk((n) => n + 1)
    }
  })
  useEffect(() => {
    if (takeSearch?.()) void searchRef.current()
  }, [takeSearch])
  useEffect(() => {
    if (searchSignal === seenSearch.current) return
    seenSearch.current = searchSignal
    void searchRef.current()
  }, [searchSignal])

  // A switch between the queue and All that nothing reopens (the kept place's own move holds its row in view) leaves the page where
  // it was, which can put the new view's first row above the window or under the sticky bar: it is brought into view once drawn.
  const reveal = useRef(false)
  const toView = async (f: LineFilter) => {
    if (await d.changeFilter(f)) reveal.current = pending.current === null
  }
  // A load that fails leaves the request unanswered, and a later render must not scroll for it.
  useEffect(() => {
    if (d.loadError) reveal.current = false
  }, [d.loadError])
  useEffect(() => {
    if (!reveal.current || d.dataFilter !== d.filter || !d.settled) return
    reveal.current = false
    d.listRef.current?.querySelector('.review-line')?.scrollIntoView?.({ block: 'nearest', behavior: 'instant' })
  })

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
  // Simple folds the player away in every view, so the lines come first. An empty queue is the exception: it has no
  // card to make room for, and the waveform holds Transcribe this gap. All has lines to show, and its gap arrows
  // bring the waveform out when used.
  const foldsMedia = d.simple && !(on && d.dataFilter === QUEUE_FILTER && d.data?.total === 0)
  const cardOnly = on && d.isPhone
  const place = cardOnly && d.data ? queuePlace(d.shown, d.activeId, d.page, PAGE_SIZE, d.data.total) : null
  // The header's steps button says where the open card is; it is told only the numbers, so its words stay its own.
  const report = usePositionReport()
  const placeAt = place?.at ?? null
  const placeTotal = place?.total ?? null
  useEffect(() => {
    report?.(placeAt !== null && placeTotal !== null ? { at: placeAt, total: placeTotal } : null)
    return () => report?.(null)
  }, [report, placeAt, placeTotal])
  const upNext = cardOnly ? nextText(d.shown, d.activeId) : null
  // Skip for now: the next card, the next page's first, or back to the start of the queue.
  const skip = () => {
    const at = d.shown.findIndex((l) => l.id === d.activeId)
    if (at < d.shown.length - 1 || d.page < d.pages) ctl.move(1)
    else if (d.page > 1) void ctl.goPage(1, 'first')
    else if (d.shown[0]) ctl.actions.activate(d.shown[0].id)
  }

  // The row waits for the main button and for the counts that name the pills, so nothing in it moves once it is drawn.
  const drawn = !checkLead || (checkLead.ready && !d.loading)
  // A phone has no switch row: the queue goes straight to its card, and All has one button back to it while lines need a look.
  const backToQueue = d.isPhone && !on ? checkActionLabel(counts?.risk ?? null) : null
  const phoneLead: ReactNode = backToQueue || checked ? (
    <>
      {backToQueue && <button type="button" className={buttonClass('primary')} onClick={() => void toView(QUEUE_FILTER)}>{backToQueue}</button>}
      {checked && <CheckedUndo line={checked} busy={undoing} onUndo={() => void undo()} onDismiss={() => setChecked(null)} />}
    </>
  ) : undefined
  const wideLead: ReactNode = (
    <>
      {checkLead && <CheckLeadAction lead={{ ...checkLead, ready: drawn }} />}
      {drawn && (
        <>
          <CheckQueueSwitch queue={on} counts={counts} onPick={(q) => void toView(q ? QUEUE_FILTER : 'all')} />
          {checked && <CheckedUndo line={checked} busy={undoing} onUndo={() => void undo()} onDismiss={() => setChecked(null)} />}
          <QueueFine counts={counts} />
        </>
      )}
    </>
  )
  const lead: ReactNode = !d.simple ? undefined : d.isPhone ? phoneLead : wideLead
  // Just after the switch is pressed, `data` is still the page of the view before: the footer waits for the queue's own.
  const after: ReactNode = on && d.data && counts && d.dataFilter === QUEUE_FILTER ? (
    <>
      <CheckQueueFooter
        dramaId={dramaId}
        queuePage={d.data}
        counts={counts}
        jobRunning={d.jobRunning}
        settled={d.settled}
        why={d.isPhone}
        onReadAll={() => void toView('all')}
        onUntranslated={() => void d.changeFilter('untranslated')}
        onChanged={d.onChanged}
      />
    </>
  ) : null
  // Drawn into the header's More panel, so it only needs to be somewhere in the tree.
  const media = d.simple && foldsMedia ? d.mediaToggle : null

  return {
    on,
    // The queue's own page is on screen (just after the switch is pressed, `data` is still the view before).
    ready: on && d.dataFilter === QUEUE_FILTER,
    foldsMedia,
    // A phone shows one card at a time.
    rows: cardOnly ? d.shown.filter((l) => l.id === d.activeId) : d.shown,
    toolbar: { lead, bare: on, searchAsk },
    // The open card's Next line, which a phone draws at the card's bottom edge.
    next: upNext,
    retranscribe,
    after: <>{media}{after}</>,
    bar: (line: ReviewLine) => (on ? { onSkip: skip, onLooksRight: () => ctl.actions.reviewAndNext(line.id) } : undefined),
  }
}

/**
 * Simple folds an audio title's player away until its Player button asks for it (remembered per viewer); the
 * player's own Show player and Show waveform choices stay as they are. `folds` is whether it would be folded, and
 * `show` unfolds it for this visit.
 */
export function useQueueMedia(audio: boolean, isPhone: boolean, sectionRef: RefObject<HTMLElement | null>, playerId: string) {
  const [open, setOpen] = usePersistedState('review.queue.media', false)
  // A folded player comes out, for this visit, when it fails (its error is in it) or a gap arrow needs the waveform.
  const [needed, setNeeded] = useState(false)
  const show = useCallback(() => setNeeded(true), [])
  // Set by the phone's Player button, which sits under the card: the player opens above it, out of sight.
  const reveal = useRef(false)
  useEffect(() => {
    if (!reveal.current || !open) return
    reveal.current = false
    const section = sectionRef.current
    if (!section) return
    // A section that starts in the first screen goes to the very top: a phone scrolled past the collapse point keeps the
    // toolbar collapsed (no player strip) until it is back near the top.
    const top = section.getBoundingClientRect().top + window.scrollY
    window.scrollTo({ top: top < window.innerHeight ? 0 : top })
  }, [open, sectionRef])
  // The button says what is on screen: a player that came out by itself reads as open, and one press folds it again.
  const shown = open || needed
  const dock = usePlayerDock()
  // Drawn in the header's More row, beside the title's other controls; the state stays here, with the player it opens.
  const toggle = audio && dock ? createPortal(
    <QueueMediaToggle
      open={shown}
      controls={playerId}
      onToggle={() => {
        reveal.current = isPhone && !shown
        setNeeded(false)
        setOpen(!shown)
      }}
    />,
    dock,
  ) : null
  return { toggle, folds: audio && !shown, show }
}

/**
 * Reports the queue to the stage, and whether its open card gets the This line tab (the tools beside the lines);
 * true once the stage hands over the tab's dock, which it does only while that tab is the one showing.
 */
export function useLineTab(queue: { on: boolean; ready: boolean }, cardOpen: boolean, dock: HTMLElement | null,
  onQueue?: (on: boolean) => void, onLineTab?: (shown: boolean) => void): boolean {
  const beside = useMediaQuery(TOOLS_BESIDE_LINES)
  const tab = queue.ready && beside && cardOpen
  useEffect(() => onQueue?.(queue.on), [queue.on, onQueue])
  useEffect(() => onLineTab?.(tab), [tab, onLineTab])
  return tab && dock !== null
}
