// The controller and builders below take ref objects and read them only later, in event handlers, which react/refs
// cannot tell from a read during render.
/* oxlint-disable react/refs */
import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

import type { PlayerMediaKind } from '../../../../api/media'
import { listAllLines } from '../../../../api/restructure'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { RetryBanner } from '../../../../components/RetryBanner'
import { readSectionOpen, writeSectionOpen } from '../../../../components/sectionStorage'
import { buttonClass } from '../../../../components/uiClasses'
import { useMediaQuery } from '../../../../hooks/useMediaQuery'
import { usePersistedState } from '../../../../hooks/usePersistedState'
import type { ReviewLine } from '../../../../types/review'
import { FindReplacePanel } from './FindReplacePanel'
import { HiddenEditBanner } from './HiddenEditBanner'
import { LineActionsSheet, type SheetState } from './LineActionsSheet'
import { useLineSelectionContext } from './useLineSelectionContext'
import { SelectionBar } from './SelectionBar'
import { selectionContext } from './selectionActions'
import { useSimple, useTitleTextLang } from '../../StageContext'
import { useCheckQueue, useLineTab, useQueueMedia } from './useCheckQueue'
import { ThisLineDock } from './ThisLinePanel'
import { LineRow, type EditState, type RowIssue } from './LineRow'
import { LinesEmpty } from './LinesEmpty'
import { buildLinesNavigation } from './linesNavigation'
import { createLinesController, type Pending } from './linesController'
import { PhoneEditBar } from './PhoneEditBar'
import { Player, type PlayerHandle } from './Player'
import { ReadFromVideo } from './ReadFromVideo'
import { ReviewWaveform } from './ReviewWaveform'
import { nextGap, prevGap } from './gapModel'
import { useGapFocus } from './useGapFocus'
import { buildSheetActions } from './lineSheetActions'
import { useLinesLoad } from './useLinesLoad'
import { useKeepPlace, useReviewPlace } from './useReviewPlace'
import { useLinkedFind } from './useLinkedFind'
import { useClearAfter, useScrollCollapse, useToolbarHeight } from './useLinesChrome'
import { useWaveformGaps } from './useWaveformGaps'
import { pageCount, pageForPosition, structureErrorText, type PanelMode } from './reviewLogic'
import { useSetSpeakerPanel } from './useSetSpeakerPanel'
import { ReviewStatus, UndoNotice } from './UndoNotice'
import { useSameFix } from './useSameFix'
import { useUndoOffer } from './undoOffer'
import type { LineTarget } from './reviewResults'
import { Pager, ReviewToolbar } from './ReviewToolbar'
import { SourceSuspectPanel } from './SourceSuspect'
import { useOpensSourcePanel, useSourceSuspects } from './useSourceSuspects'
import { ShortcutSheet } from './ShortcutSheet'
import { useStrongerOffers } from './useStrongerOffers'
import { buildStructureEdits, type UndoOffer } from './structureEdits'
import { useDraftGuard } from './useDraftGuard'
import { useCanRetranscribeLine } from './useCanRetranscribeLine'
import { useAudioToolNeed } from '../../../readiness/useAudioToolNeed'
import { useReviewShortcuts } from './useReviewShortcuts'
import { useTimingSnap } from './useTimingSnap'
import { useTmByLine } from './useTmByLine'
import { useSpeakerFill } from './useSpeakerFill'
import { LineStepButtons } from './LineStepButtons'
import { lineNumber } from '../../../../lineNumber'
import { browserStorage } from '../../../../hooks/usePersistedState'

interface Props {
  dramaId: number
  reloads: number
  onChanged: () => void
  jobRunning: boolean
  mediaKind: PlayerMediaKind | null
  // An audio file beside the video, for the player to fall back to.
  hasAudio?: boolean
  // The drama's source_language: the spoken language of a line with no lang of its own.
  sourceLanguage: string | null
  // The title's own translation engine, which "Read & translate" uses.
  translationEngine?: string | null
  // A narrated novel: lines are paragraphs, so their fake timestamps and speaker stay out of the row.
  prose?: boolean
  // The drama's whole line count, whenever the "all" view reports it.
  onLineCount?: (n: number) => void
  // The drama's flagged-line count, whenever a page reports it.
  onFlaggedCount?: (n: number) => void
  // A finding elsewhere in the stage asked to open a line; seq makes a repeat
  // click on the same line count again.
  // resolve gets null once the line is open, else a plain message.
  onCompareSelected?: () => void
  onRetimeSelected?: () => void
  // Opens the Re-transcribe section; with line ids it starts a run on them.
  // fill: the lines are new and blank (a gap, "Add and transcribe"), so what is heard goes straight in.
  onRetranscribeLines?: (lineIds: number[] | null, fill?: boolean) => void
  // Opens Records → Line history, which the Undo notice and its refusals point to.
  onOpenHistory?: () => void
  goTo?: { target: LineTarget; seq: number; resolve: (message: string | null) => void } | null
  // Whether Simple's check queue is showing, and whether it has a card open beside the tools (the This line tab).
  onQueue?: (on: boolean) => void
  onLineTab?: (shown: boolean) => void
  // The This line tab's empty panel, once the tools have drawn it: the open card's panel goes in there.
  lineDock?: HTMLElement | null
  // The side column's top slot, where a source video's card goes; null while the screen is too narrow for that column,
  // false while that slot is still being drawn (the card waits rather than being drawn inline and then moved).
  watchDock?: HTMLElement | null | false
}

const PHONE = '(max-width: 640px)'
const WIDE = '(min-width: 641px)'
const SHORT = '(max-height: 899px)'
// How long a line opened from a search result stays highlighted.
const JUMP_HIGHLIGHT_MS = 4000
const SEARCH_DEBOUNCE_MS = 300
const STATUS_MS = 8000

// The Review editor: one active line (roving tabIndex, arrows/J/K), a separate
// edit mode, the "⋯" line sheet with structure edits, a sticky toolbar with the
// player, and a phone action bar. Rows are stateless; every write goes through
// here so a dirty draft is saved (or kept, if the save fails) before moving on.

export function LinesPanel({ dramaId, reloads, onChanged, jobRunning, mediaKind, hasAudio = false, sourceLanguage, translationEngine = null, prose = false, onLineCount, onFlaggedCount, onCompareSelected, onRetimeSelected, onRetranscribeLines, onOpenHistory, goTo, onQueue, onLineTab, lineDock = null, watchDock = null }: Props) {
  const isPhone = useMediaQuery(PHONE)
  const simple = useSimple(), titleLang = useTitleTextLang()
  const isWide = useMediaQuery(WIDE) // tablets and wider: a source video gets its own sticky card beside the lines
  const isShort = useMediaQuery(SHORT)
  const collapsed = useScrollCollapse(isPhone)
  const [suspectOpen, setSuspectOpen] = useState(useOpensSourcePanel(dramaId))
  const suspects = useSourceSuspects(dramaId, reloads, suspectOpen)
  const selection = useLineSelectionContext()
  const pending = useRef<Pending | null>(null)
  const { filter, setFilter, page, setPage, place } = useReviewPlace(dramaId, simple, pending)
  const { input, setInput, term, setTerm } = useLinkedFind(dramaId)
  const [activeId, setActiveId] = useState<number | null>(null)
  const [edit, setEdit] = useState<EditState | null>(null)
  const [issue, setIssue] = useState<RowIssue | null>(null)
  const [ai, setAi] = useState<{ lineId: number; mode: PanelMode } | null>(null)
  const [sheet, setSheet] = useState<SheetState | null>(null)
  const [busy, setBusy] = useState(false)
  const busyRef = useRef(false)
  const [structError, setStructError] = useState<unknown>(null)
  const [sheetNote, setSheetNote] = useState<string | null>(null)
  const canRetranscribeLine = useCanRetranscribeLine(dramaId)
  const retranscribeNeed = useAudioToolNeed()
  const [retranscribeFocusId, setRetranscribeFocusId] = useState<number | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  // The last structural edit, while it can still be undone.
  // `at` is the edited line's position before the edit, where focus goes after an undo.
  const [undo, setUndo] = useUndoOffer<UndoOffer>('lines', dramaId)
  useEffect(() => setUndo(null), [dramaId, setUndo])
  const [keysOpen, setKeysOpen] = useState(false)
  // Row density is a per-viewer choice, remembered in localStorage.
  const [compact, setCompact] = usePersistedState('review.compact', false)
  const [replaceOpen, setReplaceOpen] = useState(() => readSectionOpen(browserStorage(), 'review.findreplace', false))

  const player = useRef<PlayerHandle>(null)
  // Phones start with the waveform folded away; elsewhere it is open.
  const [waveOpen, setWaveOpen] = usePersistedState('review.waveform', !isPhone)
  // Phones: the player's video and tools sit here, under the sticky toolbar.
  const [playerDock, setPlayerDock] = useState<HTMLDivElement | null>(null)
  // Tablets and wider, with a video: the video, seek bar and subtitles sit in the side card.
  const [sideDock, setSideDock] = useState<HTMLDivElement | null>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLUListElement>(null)
  // The line a keyboard move asked to focus; only a render that has made it the
  // active line may spend the request (a late effect from an earlier render must not).
  const focusActive = useRef<number | null>(null)
  const sectionRef = useRef<HTMLElement>(null)
  // The line just opened from a search result, highlighted for a moment (R05).
  const [jumpedId, setJumpedId] = useState<number | null>(null)
  const selectRef = useRef(selection.tick)
  useEffect(() => {
    selectRef.current = selection.tick
  })
  const showOnPageRef = useRef<(id: number) => Promise<void>>(async () => {})

  const { data, setData, found, setFound, allTotal, error, setError, retry, dataFilter, settled, ownPickRef } = useLinesLoad({
    dramaId, page, filter, term, reloads, pending, focusActive, setActiveId, setEdit, setPage,
  })

  const shown = useMemo(() => found ?? data?.lines ?? [], [found, data])
  // Ranges and "#12, #14-#18" resolve against what is on screen now.
  const { setVisible } = selection
  useEffect(() => setVisible(shown.map((l) => ({ id: l.id, number: lineNumber(l.idx) }))), [shown, setVisible])
  const pages = data ? pageCount(data.total) : 1
  const searching = term !== ''
  // Merge and add need the true neighbour, which only the All view shows.
  const limited = searching || filter !== 'all'
  // An empty drama needs no filters, search or paging: just the way forward.
  const emptyDrama = allTotal === 0 && !searching && filter === 'all'

  // A deep link opens on a filter before All loads: any matching line proves the title is not empty.
  useEffect(() => { if (allTotal !== null || data?.total) onLineCount?.(allTotal ?? data?.total ?? 0) }, [allTotal, data, onLineCount])
  useEffect(() => {
    if (data) onFlaggedCount?.(data.flagged_count)
  }, [data, onFlaggedCount])

  // Search as you type.
  useEffect(() => {
    const t = setTimeout(() => setTerm(input.trim()), SEARCH_DEBOUNCE_MS)
    return () => clearTimeout(t)
  }, [input, setTerm])

  useClearAfter(status, () => setStatus(null), STATUS_MS)

  // Keyboard moves bring the active row into view and give it focus. The
  // first load leaves the page where it is, so Review opens at its top even
  // when the starting line is further down. Rows keep clear of the sticky
  // toolbar through scroll-margin-top, which follows the toolbar's measured height.
  useEffect(() => {
    if (focusActive.current === null || focusActive.current !== activeId) return
    const el = listRef.current?.querySelector<HTMLElement>(`[data-line-id="${activeId}"]`)
    if (!el) return
    el.focus({ preventScroll: true })
    focusActive.current = null
    el.scrollIntoView?.({ block: 'nearest' })
  })
  useToolbarHeight(sectionRef)
  const showActive = () => {
    const el = activeId !== null ? listRef.current?.querySelector<HTMLElement>(`[data-line-id="${activeId}"]`) : null
    el?.scrollIntoView?.({ block: 'nearest' })
  }

  // Everything the stable row callbacks need, read at call time.
  const st = useRef({ shown, edit, page, pages, filter, searching, onChanged, jobRunning, activeId })
  useEffect(() => {
    st.current = { shown, edit, page, pages, filter, searching, onChanged, jobRunning, activeId }
  })

  const active = activeId !== null ? shown.find((l) => l.id === activeId) ?? null : null
  const fill = useSpeakerFill(dramaId, data?.no_speaker_count ?? 0, shown, reloads, active, prose, onChanged, setStatus, setError)
  const sameFix = useSameFix(dramaId, onChanged, setStatus)
  const ctl = useMemo(() => createLinesController({
    dramaId, st, pending, focusActive, player, selectRef, showOnPageRef,
    setData, setFound, setIssue, setEdit, setStatus, setActiveId, setPage, setFilter,
    setError, setSheet, setStructError, setAi, setRetranscribeFocusId, setSpeakerUndo: fill.setUndo,
    onEnglishSaved: sameFix.englishSaved,
  }), [dramaId, setData, setFound, setPage, setFilter, setError, fill.setUndo, sameFix.englishSaved])

  const { actions } = ctl

  // A draft whose row is not on screen (a reload moved it out of the filter,
  // a search, another page) gets a banner, so it can always be saved,
  // discarded or brought back into view.
  const hiddenEdit = edit && !shown.some((l) => l.id === edit.lineId) ? edit : null
  const discardHidden = () => {
    ctl.setEditNow(null)
    setIssue(null)
    setStatus(null)
  }
  const showHidden = async () => {
    if (!hiddenEdit) return
    try {
      const all = await listAllLines(dramaId)
      const pos = all.findIndex((l) => l.id === hiddenEdit.lineId)
      if (pos === -1) {
        setStatus('That line no longer exists. Discard the edit.')
        return
      }
      pending.current = { target: hiddenEdit.lineId }
      setFilter('all')
      setInput('')
      setTerm('')
      setPage(pageForPosition(pos))
    } catch (e) {
      setError(e)
    }
  }

  useDraftGuard(edit, st, ctl)

  // ---- structure edits (sheet) ----
  const sheetLine = sheet ? (shown.find((l) => l.id === sheet.lineId) ?? null) : null
  const sheetRun = sheetLine ? shown.slice(shown.findIndex((l) => l.id === sheetLine.id)) : []

  const edits = buildStructureEdits({
    dramaId, shown, sheetLine, searching, filter, page, activeId, sourceLanguage, undo, selection,
    pending, busyRef, leaveEdit: ctl.leaveEdit, focusTo: ctl.focusTo,
    setBusy, setStructError, setSheetNote, setSheet, setEdit, setAi, setPage, setStatus, setUndo, onChanged,
    onTranscribeLines: (ids) => onRetranscribeLines?.(ids, true),
  })

  const { changeFilter, changeSearch, goToLine, goToNumber } = buildLinesNavigation({
    dramaId, ctl, st, pending, listRef, searching, filter, page, place,
    setFilter, setPage, setInput, setTerm, setStatus, setError,
  })
  const goToRef = useRef(goToLine)
  useEffect(() => {
    goToRef.current = goToLine
    showOnPageRef.current = async (id) => {
      const message = await goToLine({ lineId: id })
      if (message) setStatus(message)
      else setJumpedId(id)
    }
  })
  useClearAfter(jumpedId, () => setJumpedId(null), JUMP_HIGHLIGHT_MS)
  useEffect(() => {
    if (goTo) void goToRef.current(goTo.target).then(goTo.resolve)
  }, [goTo])
  const loading = !searching && data === null && !error
  const playerId = useId()
  const media = useQueueMedia(mediaKind === 'audio' && !emptyDrama, isPhone, sectionRef, playerId)
  const queue = useCheckQueue({ dramaId, simple, filter, searching, isPhone, jobRunning, canRetranscribeLine, retranscribeNeed, data, dataFilter, settled, loadError: error, loading, allTotal, shown, activeId, page, pages, ctl, pending, listRef, st, changeFilter, onChanged, setError, mediaToggle: media.toggle })
  useKeepPlace(place, { dramaId, activeId, shown, filter, searching, term, editing: edit?.lineId ?? null, queue: queue.on, page, listRef, ownPickRef, goToLine, setStatus, setJumpedId })
  const mediaFolded = queue.foldsMedia && media.folds
  const toggleReplace = () => {
    const next = !replaceOpen
    setReplaceOpen(next)
    writeSectionOpen(browserStorage(), 'review.findreplace', next)
  }

  // Lines to offer the stronger engine for (no engine call).
  const strongerByLine = useStrongerOffers(dramaId, reloads)
  const tmByLine = useTmByLine(dramaId, shown, reloads)
  const timingByLine = useTimingSnap(dramaId, shown, reloads)

  // ---- shortcuts ----
  const linePanel = useLineTab(queue, active !== null, lineDock, onQueue, onLineTab)
  useReviewShortcuts({
    ctl, actions, selection, player, listRef, searchRef, mediaKind, isPhone,
    sheetOpen: sheet !== null, keysOpen, page, pages, searching, limited, active, edit, ai,
    setAi, setKeysOpen, setStatus, queue: queue.on, noSpeakerCount: queue.on ? 0 : fill.stepCount, activeGuesses: fill.activeGuesses,
  })

  const counts = data
    ? { all: allTotal, flagged: data.flagged_count, untranslated: data.untranslated_count, unreviewed: data.unreviewed_count ?? 0, shaky: data.shaky_count ?? 0, risk: data.risk_count ?? 0, marked: data.marked_count }
    : null
  const setSpeaker = useSetSpeakerPanel(dramaId, selection.selectedIds, onChanged)
  const selectionCtx = selectionContext(dramaId, selection, setStatus, {
    openCompare: onCompareSelected ?? (() => {}),
    openRetime: onRetimeSelected ?? (() => {}),
    openSetSpeaker: setSpeaker.open,
    openRetranscribe: (lineIds: number[]) => onRetranscribeLines?.(lineIds),
  })
  const showPager = !searching && !!data && pages > 1
  const editingActive = edit !== null && edit.lineId === activeId
  // Phones: the pager shares the player's row so the sticky toolbar stays short.
  const pagerInPlayer = isPhone && mediaKind !== null && showPager && !collapsed && !mediaFolded
  const sideVideo = isWide && !isPhone && mediaKind === 'video' && !emptyDrama
  const videoInSlot = sideVideo && watchDock !== null // its waveform then stays over the lines, where it has their width
  const looseMedia = isPhone || (isShort && !sideVideo) // pinned, they left one list line visible on a short screen

  // Tall wide screens pin it in the toolbar; phones and short screens put it under the player dock.
  const gaps = useWaveformGaps({
    dramaId,
    enabled: canRetranscribeLine && mediaKind !== null && !emptyDrama,
    refresh: reloads,
    onChanged,
    onTranscribe: (ids) => onRetranscribeLines?.(ids, true),
  })
  const openWave = (open: boolean) => {
    setWaveOpen(open)
    if (open) media.show()
  }
  const { gapFocus, gapIndex, sortedGaps, focusGap, jumpToGap } = useGapFocus({ gaps: gaps.gaps, player, setWaveOpen: openWave, setStatus })
  // The read goes in as an unsaved edit of the source text, so the usual Save,
  // Undo and conflict check still apply.
  const readIntoLine = async (patch: { zh: string; en?: string }) => {
    if (!active) {
      setStatus('Select a line first; the characters go into its source text.')
      return
    }
    if (edit?.lineId !== active.id && !(await ctl.openEdit(active.id, true))) return
    actions.setDraft(patch)
  }
  const waveform =
    mediaKind && !emptyDrama ? (
      <ReviewWaveform
        dramaId={dramaId}
        lines={shown}
        active={active}
        player={player}
        hasVideo={mediaKind === 'video'}
        open={waveOpen}
        onToggle={() => setWaveOpen(!waveOpen)}
        onRetime={ctl.retime}
        editingActive={!!active && edit !== null && edit.lineId === active.id}
        gaps={gaps}
        focusGap={gapFocus} lineCount={allTotal}
        jobRunning={jobRunning}
        canTranscribe={canRetranscribeLine}
        need={retranscribeNeed}
      />
    ) : null

  const stepButtons = !searching && !queue.on
    ? <LineStepButtons flaggedCount={data?.flagged_count ?? 0} unreviewedCount={data?.unreviewed_count ?? 0} noSpeakerCount={fill.stepCount} onMove={(d, k) => void ctl.moveFlagged(d, k)} />
    : null
  const selectAll = shown.length > 0 && !queue.on ? <SelectAllShown shown={shown} /> : null

  return (
    <section className={['review-editor', sideVideo && 'has-side', replaceOpen && 'is-replacing', mediaFolded && 'is-media-folded'].filter(Boolean).join(' ')} aria-label="Lines" ref={sectionRef}>
      <div className="review-main">
        {!emptyDrama && (
        <ReviewToolbar
          isPhone={isPhone}
          collapsed={collapsed}
          filter={filter}
          counts={counts}
          gapCount={gaps.gaps.length}
          onNextGap={() => jumpToGap(nextGap)}
          onPrevGap={() => jumpToGap(prevGap)}
          gapIndex={gapIndex}
          onGoGap={(n) => focusGap(sortedGaps[n - 1])}
          suspectCount={suspects.count}
          suspectOpen={suspectOpen}
          onToggleSuspect={() => setSuspectOpen((o) => !o)}
          onFilter={(f) => void changeFilter(f)}
          search={input}
          searchRef={searchRef}
          onSearch={(v) => void changeSearch(v)}
          resultCount={searching && found ? found.length : null}
          page={page}
          pages={pages}
          showPager={showPager && !pagerInPlayer}
          onPage={(p) => void ctl.goPage(p, 'first')}
          onGoTo={(n) => void goToNumber(n)}
          replaceOpen={replaceOpen}
          onToggleReplace={toggleReplace}
          onKeys={() => setKeysOpen(true)}
          compact={compact}
          onCompact={setCompact}
          {...queue.toolbar}
          player={
            mediaKind ? (
              <>
                <Player
                  ref={player}
                  id={playerId}
                  onFailed={media.show}
                  dramaId={dramaId}
                  kind={mediaKind} hasAudio={hasAudio}
                  lines={shown}
                  selected={active}
                  captionVersion={reloads}
                  englishAbsent={!!counts && counts.all !== null && counts.all > 0 && counts.untranslated === counts.all}
                  panelHost={looseMedia ? playerDock : sideVideo ? sideDock : undefined}
                  trailing={pagerInPlayer ? <Pager page={page} pages={pages} onPage={(p) => void ctl.goPage(p, 'first')} labelled /> : null}
                />
                {!looseMedia && (!sideVideo || videoInSlot) && waveform}
              </>
            ) : null
          }
        />
        )}
        {looseMedia && mediaKind && !emptyDrama && <div className="review-player-dock" ref={setPlayerDock} />}
        {looseMedia && waveform}
        {data && (
          <p className="sr-only" data-testid="line-counts">
            {searching && found ? `${found.length} matching` : `${data.total} in this view`} · {data.flagged_count} flagged · {data.untranslated_count} untranslated · {data.unreviewed_count ?? 0} unreviewed
          </p>
        )}
        {suspectOpen && suspects.count > 0 && (
          <SourceSuspectPanel dramaId={dramaId} items={suspects.items} misheard={suspects.misheard} onGoTo={(n) => void goToNumber(n)} onListen={mediaKind ? (l) => player.current?.playLine(l) : undefined} onChanged={onChanged} onClose={() => setSuspectOpen(false)} />
        )}
        {setSpeaker.node}
        {replaceOpen && <FindReplacePanel dramaId={dramaId} onChanged={onChanged} onClose={toggleReplace} />}
        {!simple && stepButtons}
        <ReviewStatus text={status} onOpenHistory={onOpenHistory} />
        {undo && <UndoNotice message={undo.message} busy={busy} onUndo={() => void edits.doUndo()} onDismiss={() => setUndo(null)} onOpenHistory={onOpenHistory} />}
        {sameFix.node}
        {data === null && found === null ? (
          <RetryBanner error={error} what="the lines" onRetry={retry} />
        ) : (
          <ErrorBanner error={error} onDismiss={() => setError(null)} />
        )}
        {hiddenEdit && (
          <HiddenEditBanner
            edit={hiddenEdit}
            issue={issue}
            onSave={() => void ctl.saveEdit().then((ok) => ok && ctl.setEditNow(null))}
            onShow={() => void showHidden()}
            onDiscard={discardHidden}
          />
        )}

        {loading && (
          <ul className="review-lines" aria-hidden="true">
            {Array.from({ length: 6 }, (_, i) => (
              <li key={i} className="review-line review-skeleton" />
            ))}
          </ul>
        )}
        {!loading && shown.length === 0 && !error && (
          <LinesEmpty
            dramaId={dramaId}
            filter={filter}
            term={term}
            searching={searching}
            jobRunning={jobRunning}
            onAddFirst={() => void ctl.openSheet(null, 'add')}
            onAllLines={() => void changeFilter('all')}
          />
        )}
        {/* Simple puts the step buttons and Select all shown on one row, so the first line is on the first screen. */}
        {simple ? (stepButtons || selectAll) && <div className="review-list-head">{selectAll}{stepButtons}</div> : selectAll}
        {/* Simple's wide screens draw the rows, the pager and the queue's footer as one white card. */}
        <div className={simple ? 'review-list-card' : 'review-list-plain'}>
        <ul className={['review-lines', compact && 'is-compact', selection.count > 0 && 'has-selection'].filter(Boolean).join(' ')} ref={listRef}>
          {queue.rows.map((l) => (
            <LineRow
              key={l.id}
              dramaId={dramaId}
              line={l}
              sourceLanguage={sourceLanguage} titleLang={titleLang} prose={prose}
              active={l.id === activeId}
              selected={selection.selectedSet.has(l.id)}
              isPhone={isPhone}
              hasMedia={mediaKind !== null}
              jobRunning={jobRunning}
              limited={limited}
              edit={edit?.lineId === l.id ? edit : null}
              ai={ai?.lineId === l.id ? ai.mode : null}
              tm={tmByLine.get(l.id) ?? null}
              stronger={strongerByLine.get(l.id) ?? null} guesses={fill.guessesByLine.get(l.id) ?? null} speakerNote={fill.noteFor(l.id)}
              timingSnap={timingByLine.get(l.id) ?? null}
              issue={issue?.lineId === l.id ? issue : null}
              actions={actions}
              simple={simple}
              queue={queue.on}
              queueNext={queue.next}
              linePanel={linePanel}
              retranscribe={queue.retranscribe}
              searchHit={searching}
              jumped={jumpedId === l.id}
              focusRetranscribe={retranscribeFocusId === l.id}
            />
          ))}
        </ul>
        {fill.undoNode}
        {selection.count > 0 && <SelectionBar ctx={selectionCtx} />}
        {showPager && <div className="review-bottom-pager"><Pager page={page} pages={pages} onPage={(p) => void ctl.goPage(p, 'first')} /></div>}
        {queue.after}
        </div>

        {isPhone && active && (
          <PhoneEditBar
            active={active}
            editing={editingActive}
            discardWarning={editingActive && !!edit?.discardWarning}
            hasMedia={mediaKind !== null}
            onCancel={actions.cancelEdit}
            onSave={actions.save}
            onSaveAndNext={actions.saveAndNext}
            onPlay={() => {
              showActive()
              player.current?.toggleLine(active)
            }}
            onMove={ctl.move}
            onEdit={() => {
              showActive()
              void ctl.openEdit(active.id)
            }}
            queue={queue.bar(active)}
          />
        )}

        <LineActionsSheet
          dramaId={dramaId}
          state={sheet}
          line={sheetLine}
          run={sheetRun}
          hasMedia={mediaKind !== null}
          jobRunning={jobRunning}
          busy={busy}
          error={structError}
          errorText={sheetNote ?? structureErrorText(structError)}
          limited={limited}
          canRetranscribe={canRetranscribeLine}
          retranscribeNeed={retranscribeNeed}
          timingSnap={sheetLine ? timingByLine.get(sheetLine.id) ?? null : null}
          sourceLanguage={sourceLanguage}
          actions={buildSheetActions({ sheetLine, ctl, edits, player, setSheet, setStructError, setSheetNote })}
        />
        <ShortcutSheet open={keysOpen} onClose={() => setKeysOpen(false)} />
      </div>
      {linePanel && active && lineDock && (
        <ThisLineDock dock={lineDock} dramaId={dramaId} line={active} sound={mediaKind === 'audio'} player={player} reloads={reloads}
                      sourceLanguage={sourceLanguage} onShowLine={actions.showOnPage} />
      )}
      {sideVideo && watchDock !== false && (
        <WatchCard slot={watchDock}>
          <div className="review-watch-dock" ref={setSideDock} />{!videoInSlot && waveform}
          <ReadFromVideo
            player={player}
            sourceLanguage={sourceLanguage ?? ''}
            lineStart={active ? active.start : null}
            onText={(zh) => void readIntoLine({ zh })} translationEngine={translationEngine} onReadTranslated={(zh, en) => void readIntoLine({ zh, en })} dramaId={dramaId} lines={shown} onBatchSaved={onChanged}
          />
        </WatchCard>
      )}
    </section>
  )
}

// The source video's card: beside the lines, or on wide screens in the side column's top slot (`slot`).
function WatchCard({ slot, children }: { slot: HTMLElement | null; children: ReactNode }) {
  const card = (
    <aside className="card review-watch" aria-label="Video with subtitles">
      <h2 className="card-title">Video with subtitles</h2>
      {children}
    </aside>
  )
  return slot ? createPortal(card, slot) : card
}

function SelectAllShown({ shown }: { shown: ReviewLine[] }) {
  const selection = useLineSelectionContext()
  const allShownSelected = shown.every((l) => selection.selectedSet.has(l.id))
  return (
    <div className="review-selectall" role="group" aria-label="Select lines">
      <button type="button" className={buttonClass('ghost', 'sm')} disabled={allShownSelected} onClick={() => selection.selectMany(shown.map((l) => l.id))}>
        Select all shown
      </button>
      {selection.count > 0 && (
        <>
          <button type="button" className={buttonClass('ghost', 'sm')} onClick={selection.clear}>Clear</button>
          <span role="status" data-testid="selection-count">{selection.count} selected</span>
        </>
      )}
    </div>
  )
}
