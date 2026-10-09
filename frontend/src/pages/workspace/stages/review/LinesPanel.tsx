import { useEffect, useMemo, useRef, useState } from 'react'

import type { MediaKind } from '../../../../api/media'
import { listAllLines } from '../../../../api/restructure'
import { listLines, searchLines } from '../../../../api/review'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { readSectionOpen, writeSectionOpen } from '../../../../components/sectionStorage'
import { buttonClass } from '../../../../components/uiClasses'
import { useMediaQuery } from '../../../../hooks/useMediaQuery'
import { usePersistedState } from '../../../../hooks/usePersistedState'
import type { LineFilter, ReviewLine, ReviewLinesPage } from '../../../../types/review'
import { FindReplacePanel } from './FindReplacePanel'
import { HiddenEditBanner } from './HiddenEditBanner'
import { LineActionsSheet, type SheetState } from './LineActionsSheet'
import { useLineSelectionContext } from './LineSelectionContext'
import { SelectionBar } from './SelectionBar'
import { LineRow, type EditState, type RowIssue } from './LineRow'
import { LinesEmpty } from './LinesEmpty'
import { buildLinesNavigation } from './linesNavigation'
import { createLinesController, type Pending, type Target } from './linesController'
import { PhoneEditBar } from './PhoneEditBar'
import { Player, type PlayerHandle } from './Player'
import { ReviewWaveform } from './ReviewWaveform'
import { draftFromLine } from './reviewDraft'
import { initialActiveId, PAGE_SIZE, pageCount, pageForPosition, structureErrorText, type PanelMode } from './reviewLogic'
import { UndoNotice } from './UndoNotice'
import { useUndoOffer } from './undoOffer'
import type { LineTarget } from './reviewResults'
import { Pager, ReviewToolbar } from './ReviewToolbar'
import { ShortcutSheet } from './ShortcutSheet'
import { useStrongerOffers } from './useStrongerOffers'
import { DRAFT_NOT_SAVED, buildStructureEdits, type UndoOffer } from './structureEdits'
import { useDraftGuard } from './useDraftGuard'
import { useCanRetranscribeLine } from './useCanRetranscribeLine'
import { useReviewShortcuts } from './useReviewShortcuts'
import { useTimingSnap } from './useTimingSnap'
import { useTmByLine } from './useTmByLine'
import { lineNumber } from '../../../../lineNumber'

interface Props {
  dramaId: number
  reloads: number
  onChanged: () => void
  jobRunning: boolean
  mediaKind: MediaKind | null
  // The drama's source_language: the spoken language of a line with no lang of its own.
  sourceLanguage: string | null
  // The drama's whole line count, whenever the "all" view reports it.
  onLineCount?: (n: number) => void
  // The drama's flagged-line count, whenever a page reports it.
  onFlaggedCount?: (n: number) => void
  // A finding elsewhere in the stage asked to open a line; seq makes a repeat
  // click on the same line count again.
  // resolve gets null once the line is open, else a plain message.
  onCompareSelected?: () => void
  onRetimeSelected?: () => void
  goTo?: { target: LineTarget; seq: number; resolve: (message: string | null) => void } | null
}

const PHONE = '(max-width: 640px)'
const WIDE = '(min-width: 641px)'
// How long a line opened from a search result stays highlighted.
const JUMP_HIGHLIGHT_MS = 4000
const SEARCH_DEBOUNCE_MS = 300
const STATUS_MS = 8000

function browserStorage() {
  try {
    return typeof window === 'undefined' ? null : window.localStorage
  } catch {
    return null
  }
}

function pick(lines: ReviewLine[], t: Target): ReviewLine | undefined {
  if (t === 'first') return lines[0]
  if (t === 'last') return lines[lines.length - 1]
  return lines.find((l) => l.id === t)
}

// The Review editor: one active line (roving tabIndex, arrows/J/K), a separate
// edit mode, the "⋯" line sheet with structure edits, a sticky toolbar with the
// player, and a phone action bar. Rows are stateless; every write goes through
// here so a dirty draft is saved (or kept, if the save fails) before moving on.

export function LinesPanel({ dramaId, reloads, onChanged, jobRunning, mediaKind, sourceLanguage, onLineCount, onFlaggedCount, onCompareSelected, onRetimeSelected, goTo }: Props) {
  const isPhone = useMediaQuery(PHONE)
  // Tablets and wider: a source video gets its own sticky card beside the lines.
  const isWide = useMediaQuery(WIDE)
  const selection = useLineSelectionContext()
  const [filter, setFilter] = useState<LineFilter>('all')
  const [page, setPage] = useState(1)
  const [input, setInput] = useState('')
  const [term, setTerm] = useState('')
  const [data, setData] = useState<ReviewLinesPage | null>(null)
  const [found, setFound] = useState<ReviewLine[] | null>(null)
  const [allTotal, setAllTotal] = useState<number | null>(null)
  const [error, setError] = useState<unknown>(null)
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
  const loadedOnce = useRef(false)
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

  useEffect(() => {
    if (!status) return
    const t = setTimeout(() => setStatus(null), STATUS_MS)
    return () => clearTimeout(t)
  }, [status])

  useEffect(() => {
    let cancelled = false
    const settle = (lines: ReviewLine[]) => {
      const p = pending.current
      pending.current = null
      const target = p ? pick(lines, p.target) : undefined
      if (target) {
        setActiveId(target.id)
        focusActive.current = target.id
        if (p?.edit) setEdit({ lineId: target.id, base: target, draft: draftFromLine(target), details: false, note: null })
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
  }, [dramaId, page, filter, term, reloads])

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
  useEffect(() => {
    const section = sectionRef.current
    const bar = section?.querySelector<HTMLElement>('.review-toolbar')
    if (!section || !bar || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => section.style.setProperty('--review-toolbar-h', `${bar.offsetHeight}px`))
    ro.observe(bar)
    return () => ro.disconnect()
  })
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
  })

  const closeSheetThen = (fn: () => void) => {
    setSheet(null)
    fn()
  }

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
  useEffect(() => {
    if (jumpedId === null) return
    const t = setTimeout(() => setJumpedId(null), JUMP_HIGHLIGHT_MS)
    return () => clearTimeout(t)
  }, [jumpedId])
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
  }
  const allShownSelected = shown.length > 0 && shown.every((l) => selection.selectedSet.has(l.id))
  const loading = !searching && data === null && !error
  const showPager = !searching && !!data && pages > 1
  const editingActive = edit !== null && edit.lineId === activeId
  // Phones: the pager shares the player's row so the sticky toolbar stays short.
  const pagerInPlayer = isPhone && mediaKind !== null && showPager
  const sideVideo = isWide && !isPhone && mediaKind === 'video' && !emptyDrama

  // Wider screens keep it in the sticky toolbar so it stays in view while the
  // list scrolls; phones put it under the player dock, where it scrolls away.
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
      />
    ) : null

  return (
    <section className={sideVideo ? 'review-editor has-side' : 'review-editor'} aria-label="Lines" ref={sectionRef}>
      <div className="review-main">
        {!emptyDrama && (
        <ReviewToolbar
          isPhone={isPhone}
          filter={filter}
          counts={counts}
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
            {data.total} in this view · {data.flagged_count} flagged · {data.untranslated_count} untranslated
          </p>
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
        <ErrorBanner error={error} onDismiss={() => setError(null)} />
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
          onView={(view) => {
            setStructError(null)
            setSheetNote(null)
            // Split/merge/add work on saved text: save a draft first, stay on the menu if that fails.
            void (view === 'menu' ? Promise.resolve(true) : ctl.leaveEdit()).then((ok) => {
              if (ok) setSheet((s) => (s ? { ...s, view, armDelete: false } : s))
              else setSheetNote(DRAFT_NOT_SAVED)
            })
          }}
          onClose={() => setSheet(null)}
          onPlayRange={(start, end) => sheetLine && player.current?.playLine({ id: sheetLine.id, idx: sheetLine.idx, start, end })}
          onPlay={() => closeSheetThen(() => sheetLine && player.current?.playLine(sheetLine))}
          onEditDetails={() => closeSheetThen(() => sheetLine && void ctl.openEdit(sheetLine.id, true))}
          canRetranscribe={canRetranscribeLine}
          onRetranscribe={() =>
            closeSheetThen(() => {
              if (!sheetLine) return
              const id = sheetLine.id
              void ctl.openEdit(id, true).then((ok) => ok && setRetranscribeFocusId(id))
            })
          }
          onImprove={() => closeSheetThen(() => sheetLine && actions.setAi(sheetLine.id, 'improve'))}
          onWhy={() => closeSheetThen(() => sheetLine && actions.setAi(sheetLine.id, 'explain'))}
          onTool={(mode) => closeSheetThen(() => sheetLine && actions.setAi(sheetLine.id, mode))}
          onDismissFlag={() => closeSheetThen(() => sheetLine && actions.dismissFlag(sheetLine.id))}
          onAddNote={() =>
            closeSheetThen(() => {
              if (!sheetLine) return
              const id = sheetLine.id
              void ctl.openEdit(id, true).then((ok) => ok && actions.setNote({ term: '', type: 'translation', text: '' }))
            })
          }
          onSplit={edits.doSplit}
          onMerge={edits.doMerge}
          onAdd={edits.doAdd}
          onDelete={edits.doDelete}
          sourceLanguage={sourceLanguage}
          onSetLanguage={(lang, scope) => void edits.doSetLanguage(lang, scope)}
        />
        <ShortcutSheet open={keysOpen} onClose={() => setKeysOpen(false)} />
      </div>
      {sideVideo && (
        <aside className="card review-watch" aria-label="Video with subtitles">
          <h3 className="card-title">Video with subtitles</h3>
          <div className="review-watch-dock" ref={setSideDock} />
        </aside>
      )}
    </section>
  )
}
