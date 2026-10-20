import { useEffect, useMemo, useRef, useState } from 'react'

import type { PlayerMediaKind } from '../../../../api/media'
import { listAllLines } from '../../../../api/restructure'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { RetryBanner } from '../../../../components/RetryBanner'
import { readSectionOpen, writeSectionOpen } from '../../../../components/sectionStorage'
import { buttonClass } from '../../../../components/uiClasses'
import { useMediaQuery } from '../../../../hooks/useMediaQuery'
import { usePersistedState } from '../../../../hooks/usePersistedState'
import { useRoute } from '../../../../router'
import type { LineFilter } from '../../../../types/review'
import { FindReplacePanel } from './FindReplacePanel'
import { HiddenEditBanner } from './HiddenEditBanner'
import { LineActionsSheet, type SheetState } from './LineActionsSheet'
import { useLineSelectionContext } from './LineSelectionContext'
import { SelectionBar } from './SelectionBar'
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
import { useClearAfter, useScrollCollapse, useToolbarHeight } from './useLinesChrome'
import { useWaveformGaps } from './useWaveformGaps'
import { pageCount, pageForPosition, structureErrorText, type PanelMode } from './reviewLogic'
import { UndoNotice } from './UndoNotice'
import { useUndoOffer } from './undoOffer'
import type { LineTarget } from './reviewResults'
import { Pager, ReviewToolbar } from './ReviewToolbar'
import { SourceSuspectPanel } from './SourceSuspect'
import { useSourceSuspects } from './useSourceSuspects'
import { ShortcutSheet } from './ShortcutSheet'
import { useStrongerOffers } from './useStrongerOffers'
import { buildStructureEdits, type UndoOffer } from './structureEdits'
import { useDraftGuard } from './useDraftGuard'
import { useCanRetranscribeLine } from './useCanRetranscribeLine'
import { useAudioToolNeed } from '../../../readiness/useAudioToolNeed'
import { useReviewShortcuts } from './useReviewShortcuts'
import { useTimingSnap } from './useTimingSnap'
import { useTmByLine } from './useTmByLine'
import { lineNumber } from '../../../../lineNumber'
import { browserStorage } from '../../../../hooks/usePersistedState'

interface Props {
  dramaId: number
  reloads: number
  onChanged: () => void
  jobRunning: boolean
  mediaKind: PlayerMediaKind | null
  // The drama's source_language: the spoken language of a line with no lang of its own.
  sourceLanguage: string | null
  // The title's own translation engine, which "Read & translate" uses.
  translationEngine?: string | null
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
  onRetranscribeLines?: (lineIds: number[] | null) => void
  goTo?: { target: LineTarget; seq: number; resolve: (message: string | null) => void } | null
}

const PHONE = '(max-width: 640px)'
const WIDE = '(min-width: 641px)'
// How long a line opened from a search result stays highlighted.
const JUMP_HIGHLIGHT_MS = 4000
const SEARCH_DEBOUNCE_MS = 300
const STATUS_MS = 8000

// The Review editor: one active line (roving tabIndex, arrows/J/K), a separate
// edit mode, the "⋯" line sheet with structure edits, a sticky toolbar with the
// player, and a phone action bar. Rows are stateless; every write goes through
// here so a dirty draft is saved (or kept, if the save fails) before moving on.

export function LinesPanel({ dramaId, reloads, onChanged, jobRunning, mediaKind, sourceLanguage, translationEngine = null, onLineCount, onFlaggedCount, onCompareSelected, onRetimeSelected, onRetranscribeLines, goTo }: Props) {
  const isPhone = useMediaQuery(PHONE)
  // Tablets and wider: a source video gets its own sticky card beside the lines.
  const isWide = useMediaQuery(WIDE)
  const collapsed = useScrollCollapse(isPhone)
  const suspects = useSourceSuspects(dramaId, reloads)
  const [suspectOpen, setSuspectOpen] = useState(false)
  const selection = useLineSelectionContext()
  const route = useRoute()
  // A warning elsewhere links here with ?focus=flagged to show those lines first.
  const [filter, setFilter] = useState<LineFilter>(() =>
    route.name === 'drama' && route.focus === 'flagged' ? 'flagged' : 'all')
  const [page, setPage] = useState(1)
  const [input, setInput] = useState('')
  const [term, setTerm] = useState('')
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
  const pending = useRef<Pending | null>(null)
  // The line a keyboard move asked to focus; only a render that has made it the
  // active line may spend the request (a late effect from an earlier render must not).
  const focusActive = useRef<number | null>(null)
  const scrollActive = useRef(false)
  const sectionRef = useRef<HTMLElement>(null)
  // The line just opened from a search result, highlighted for a moment (R05).
  const [jumpedId, setJumpedId] = useState<number | null>(null)
  const selectRef = useRef(selection.tick)
  useEffect(() => {
    selectRef.current = selection.tick
  })
  const showOnPageRef = useRef<(id: number) => Promise<void>>(async () => {})

  const { data, setData, found, setFound, allTotal, error, setError, retry } = useLinesLoad({
    dramaId, page, filter, term, reloads, pending, focusActive, scrollActive, setActiveId, setEdit,
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

  useEffect(() => {
    if (allTotal !== null) onLineCount?.(allTotal)
  }, [allTotal, onLineCount])
  useEffect(() => {
    if (data) onFlaggedCount?.(data.flagged_count)
  }, [data, onFlaggedCount])

  // Search as you type.
  useEffect(() => {
    const t = setTimeout(() => setTerm(input.trim()), SEARCH_DEBOUNCE_MS)
    return () => clearTimeout(t)
  }, [input])

  useClearAfter(status, () => setStatus(null), STATUS_MS)

  // Keyboard moves bring the active row into view and give it focus; the
  // first load only scrolls. Rows keep clear of the sticky toolbar through
  // scroll-margin-top, which follows the toolbar's measured height.
  useEffect(() => {
    if ((focusActive.current === null && !scrollActive.current) || activeId === null) return
    if (focusActive.current !== null && focusActive.current !== activeId) return
    const el = listRef.current?.querySelector<HTMLElement>(`[data-line-id="${activeId}"]`)
    if (!el) return
    if (focusActive.current !== null) el.focus({ preventScroll: true })
    focusActive.current = null
    scrollActive.current = false
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

  const ctl = useMemo(
    () =>
      createLinesController({
        dramaId, st, pending, focusActive, player, selectRef, showOnPageRef,
        setData, setFound, setIssue, setEdit, setStatus, setActiveId, setPage, setFilter,
        setError, setSheet, setStructError, setAi, setRetranscribeFocusId,
      }),
    [dramaId],
  )

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
    onTranscribeLines: (ids) => onRetranscribeLines?.(ids),
  })

  const { changeFilter, changeSearch, goToLine, goToNumber } = buildLinesNavigation({
    dramaId, ctl, st, pending, listRef, searching, filter, page,
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
  const active = activeId !== null ? shown.find((l) => l.id === activeId) ?? null : null
  useReviewShortcuts({
    ctl, actions, selection, player, listRef, searchRef, mediaKind, isPhone,
    sheetOpen: sheet !== null, keysOpen, page, pages, searching, limited, active, edit, ai,
    setAi, setKeysOpen, setStatus,
  })

  const counts = data
    ? { all: allTotal, flagged: data.flagged_count, untranslated: data.untranslated_count }
    : null
  const selectionCtx = {
    dramaId,
    selectedIds: selection.selectedIds,
    lineNumbers: selection.lineNumbers(selection.selectedIds),
    clear: selection.clear,
    notify: setStatus,
    openCompare: onCompareSelected ?? (() => {}),
    openRetime: onRetimeSelected ?? (() => {}),
    openRetranscribe: (lineIds: number[]) => onRetranscribeLines?.(lineIds),
  }
  const allShownSelected = shown.length > 0 && shown.every((l) => selection.selectedSet.has(l.id))
  const loading = !searching && data === null && !error
  const showPager = !searching && !!data && pages > 1
  const editingActive = edit !== null && edit.lineId === activeId
  // Phones: the pager shares the player's row so the sticky toolbar stays short.
  const pagerInPlayer = isPhone && mediaKind !== null && showPager && !collapsed
  const sideVideo = isWide && !isPhone && mediaKind === 'video' && !emptyDrama

  // Wider screens keep it in the sticky toolbar so it stays in view while the
  // list scrolls; phones put it under the player dock, where it scrolls away.
  const gaps = useWaveformGaps({
    dramaId,
    enabled: canRetranscribeLine && mediaKind !== null && !emptyDrama,
    refresh: reloads,
    onChanged,
    onTranscribe: (ids) => onRetranscribeLines?.(ids),
  })
  const { gapFocus, gapIndex, sortedGaps, focusGap, jumpToGap } = useGapFocus({ gaps: gaps.gaps, player, setWaveOpen, setStatus })
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
        open={waveOpen}
        onToggle={() => setWaveOpen(!waveOpen)}
        onRetime={ctl.retime}
        editingActive={!!active && edit !== null && edit.lineId === active.id}
        gaps={gaps}
        focusGap={gapFocus}
        jobRunning={jobRunning}
        canTranscribe={canRetranscribeLine}
        need={retranscribeNeed}
      />
    ) : null

  return (
    <section className={sideVideo ? 'review-editor has-side' : 'review-editor'} aria-label="Lines" ref={sectionRef}>
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
          suspectCount={suspects.length}
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
          player={
            mediaKind ? (
              <>
                <Player
                  ref={player}
                  dramaId={dramaId}
                  kind={mediaKind}
                  lines={shown}
                  selected={active}
                  captionVersion={reloads}
                  panelHost={isPhone ? playerDock : sideVideo ? sideDock : undefined}
                  trailing={pagerInPlayer ? <Pager page={page} pages={pages} onPage={(p) => void ctl.goPage(p, 'first')} labelled /> : null}
                />
                {!isPhone && waveform}
              </>
            ) : null
          }
        />
        )}
        {isPhone && mediaKind && !emptyDrama && <div className="review-player-dock" ref={setPlayerDock} />}
        {isPhone && waveform}
        {data && (
          <p className="sr-only" data-testid="line-counts">
            {searching && found ? `${found.length} matching` : `${data.total} in this view`} · {data.flagged_count} flagged · {data.untranslated_count} untranslated
          </p>
        )}
        {suspectOpen && suspects.length > 0 && (
          <SourceSuspectPanel dramaId={dramaId} items={suspects} onGoTo={(n) => void goToNumber(n)} onChanged={onChanged} onClose={() => setSuspectOpen(false)} />
        )}
        {replaceOpen && <FindReplacePanel dramaId={dramaId} onChanged={onChanged} onClose={toggleReplace} />}
        {!searching && !!data?.flagged_count && (
          <div className="review-flagnav" role="group" aria-label="Flagged lines">
            <button type="button" className={buttonClass('secondary', 'sm')} title="Previous flagged line (Alt+↑)" onClick={() => void ctl.moveFlagged(-1)}>
              ‹ Previous flagged
            </button>
            <button type="button" className={buttonClass('secondary', 'sm')} title="Next flagged line (Alt+↓)" onClick={() => void ctl.moveFlagged(1)}>
              Next flagged ›
            </button>
          </div>
        )}
        <p className="review-status" role="status">
          {status}
        </p>
        {undo && <UndoNotice message={undo.message} busy={busy} onUndo={() => void edits.doUndo()} onDismiss={() => setUndo(null)} />}
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
        {shown.length > 0 && (
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
        )}
        <ul className={['review-lines', compact && 'is-compact', selection.count > 0 && 'has-selection'].filter(Boolean).join(' ')} ref={listRef}>
          {shown.map((l) => (
            <LineRow
              key={l.id}
              dramaId={dramaId}
              line={l}
              sourceLanguage={sourceLanguage}
              active={l.id === activeId}
              selected={selection.selectedSet.has(l.id)}
              isPhone={isPhone}
              hasMedia={mediaKind !== null}
              jobRunning={jobRunning}
              limited={limited}
              edit={edit?.lineId === l.id ? edit : null}
              ai={ai?.lineId === l.id ? ai.mode : null}
              tm={tmByLine.get(l.id) ?? null}
              stronger={strongerByLine.get(l.id) ?? null}
              timingSnap={timingByLine.get(l.id) ?? null}
              issue={issue?.lineId === l.id ? issue : null}
              actions={actions}
              searchHit={searching}
              jumped={jumpedId === l.id}
              focusRetranscribe={retranscribeFocusId === l.id}
            />
          ))}
        </ul>
        {selection.count > 0 && <SelectionBar ctx={selectionCtx} />}
        {showPager && (
          <div className="review-bottom-pager">
            <Pager page={page} pages={pages} onPage={(p) => void ctl.goPage(p, 'first')} />
          </div>
        )}

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
          actions={buildSheetActions({ sheetLine, ctl, edits, player, setSheet, setStructError, setSheetNote, setRetranscribeFocusId })}
        />
        <ShortcutSheet open={keysOpen} onClose={() => setKeysOpen(false)} />
      </div>
      {sideVideo && (
        <aside className="card review-watch" aria-label="Video with subtitles">
          <h3 className="card-title">Video with subtitles</h3>
          <div className="review-watch-dock" ref={setSideDock} />
          <ReadFromVideo
            player={player}
            sourceLanguage={sourceLanguage ?? ''}
            lineStart={active ? active.start : null}
            onText={(zh) => void readIntoLine({ zh })} translationEngine={translationEngine} onReadTranslated={(zh, en) => void readIntoLine({ zh, en })} dramaId={dramaId} lines={shown} onBatchSaved={onChanged}
          />
        </aside>
      )}
    </section>
  )
}
