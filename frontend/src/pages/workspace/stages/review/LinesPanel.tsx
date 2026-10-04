import { useEffect, useMemo, useRef, useState } from 'react'

import { ApiError } from '../../../../api/client'
import type { MediaKind } from '../../../../api/media'
import { addLine, deleteLine, listAllLines, mergeLines, splitLine } from '../../../../api/restructure'
import {
  acceptTm as acceptTmSuggestion,
  addNote,
  dismissFlag,
  flaggedAdjacent,
  listLines,
  listTmSuggestions,
  patchLine,
  searchLines,
  setLinesLanguage,
} from '../../../../api/review'
import { ButtonLink } from '../../../../components/Button'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { readSectionOpen, writeSectionOpen } from '../../../../components/sectionStorage'
import { buttonClass } from '../../../../components/uiClasses'
import { useMediaQuery } from '../../../../hooks/useMediaQuery'
import { usePersistedState } from '../../../../hooks/usePersistedState'
import { useShortcut } from '../../../../hooks/useShortcut'
import { routeHref } from '../../../../router'
import type { RestructureResult } from '../../../../types/restructure'
import type { LineFilter, ReviewLine, ReviewLinesPage, TmSuggestion } from '../../../../types/review'
import type { NewLine } from './AddLineForm'
import { FindReplacePanel } from './FindReplacePanel'
import { LineActionsSheet, type SheetState, type SheetView } from './LineActionsSheet'
import { LineRow, type EditState, type NoteDraft, type RowActions, type RowIssue } from './LineRow'
import { Player, type PlayerHandle } from './Player'
import {
  adjacentRun,
  buildPatch,
  charCount,
  codePointOffset,
  draftFromLine,
  emptyMessage,
  initialActiveId,
  isDirty,
  languageSetText,
  lineRange,
  flaggedStep,
  nextFlaggedId,
  PAGE_SIZE,
  pageCount,
  pageForPosition,
  pageStillMatches,
  stepFrom,
  structureErrorText,
  suggestionPatch,
  type LanguageScope,
  type LineDraft,
  type PanelMode,
} from './reviewLogic'
import type { LineTarget } from './reviewResults'
import { Pager, ReviewToolbar } from './ReviewToolbar'
import { ShortcutSheet } from './ShortcutSheet'
import { useStrongerOffers } from './useStrongerOffers'
import type { SplitChoice } from './SplitDialog'
import { dismissTmEverywhere, useTmDismissed, visibleTm } from './tmDismiss'
import { idxFromLineNumber, lineNumber } from '../../../../lineNumber'

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
  goTo?: { target: LineTarget; seq: number; resolve: (message: string | null) => void } | null
}

type Target = 'first' | 'last' | number
type Pending = { target: Target; edit?: boolean }

const PHONE = '(max-width: 640px)'
const WIDE = '(min-width: 641px)'
// How long a line opened from a search result stays highlighted.
const JUMP_HIGHLIGHT_MS = 4000
const ALL_LINES_ONLY = 'Merge and add work in the All lines view (no filter or search).'
const DRAFT_NOT_SAVED = 'Your edit to this line could not be saved, so nothing else was changed. Close this and check the line.'
const SEARCH_DEBOUNCE_MS = 300
const STATUS_MS = 8000

function browserStorage() {
  try {
    return typeof window === 'undefined' ? null : window.localStorage
  } catch {
    return null
  }
}

const mismatch = () => new ApiError(409, { code: 'conflict', message: 'lines changed' })

function pick(lines: ReviewLine[], t: Target): ReviewLine | undefined {
  if (t === 'first') return lines[0]
  if (t === 'last') return lines[lines.length - 1]
  return lines.find((l) => l.id === t)
}

// The Review editor: one active line (roving tabIndex, arrows/J/K), a separate
// edit mode, the "⋯" line sheet with structure edits, a sticky toolbar with the
// player, and a phone action bar. Rows are stateless; every write goes through
// here so a dirty draft is saved (or kept, if the save fails) before moving on.
export function LinesPanel({ dramaId, reloads, onChanged, jobRunning, mediaKind, sourceLanguage, onLineCount, onFlaggedCount, goTo }: Props) {
  const isPhone = useMediaQuery(PHONE)
  // Tablets and wider: a source video gets its own sticky card beside the lines.
  const isWide = useMediaQuery(WIDE)
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
  // Translation-memory suggestions for the lines shown (R11), by line id.
  const [tmList, setTmList] = useState<TmSuggestion[]>([])
  const tmDismissed = useTmDismissed(dramaId)
  const [sheet, setSheet] = useState<SheetState | null>(null)
  const [busy, setBusy] = useState(false)
  const busyRef = useRef(false)
  const [structError, setStructError] = useState<unknown>(null)
  const [sheetNote, setSheetNote] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [keysOpen, setKeysOpen] = useState(false)
  // Row density is a per-viewer choice, remembered in localStorage.
  const [compact, setCompact] = usePersistedState('review.compact', false)
  const [replaceOpen, setReplaceOpen] = useState(() => readSectionOpen(browserStorage(), 'review.findreplace', false))

  const player = useRef<PlayerHandle>(null)
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
  const showOnPageRef = useRef<(id: number) => Promise<void>>(async () => {})

  const shown = useMemo(() => found ?? data?.lines ?? [], [found, data])
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

  const ctl = useMemo(() => {
    const find = (id: number | null) => st.current.shown.find((l) => l.id === id) ?? null
    const replaceLine = (saved: ReviewLine) => {
      setData((d) => (d ? { ...d, lines: d.lines.map((l) => (l.id === saved.id ? saved : l)) } : d))
      setFound((f) => (f ? f.map((l) => (l.id === saved.id ? saved : l)) : f))
      st.current.shown = st.current.shown.map((l) => (l.id === saved.id ? saved : l))
    }
    const failLine = (lineId: number, e: unknown) => {
      if (e instanceof ApiError && e.status === 409) setIssue({ lineId, conflict: true })
      else setIssue({ lineId, error: e })
    }
    const setEditNow = (e: EditState | null) => {
      st.current.edit = e
      setEdit(e)
    }

    // Save the open draft; true when nothing is left unsaved. One save at a
    // time: a second request (a double Ctrl+S) waits, then re-checks the draft
    // against the saved line instead of sending a stale compare-and-set.
    const saving: { current: Promise<boolean> | null } = { current: null }
    const saveEdit = (): Promise<boolean> => {
      if (saving.current) return saving.current.then(() => saveEdit())
      const run = saveDraft().finally(() => {
        saving.current = null
      })
      saving.current = run
      return run
    }
    const saveDraft = async (): Promise<boolean> => {
      const cur = st.current.edit
      if (!cur) return true
      // The base, not the listed row: the row may have left the view (a
      // filter, a dismissed flag) and the draft must still be saved.
      const line = cur.base
      const patch = buildPatch(line, cur.draft)
      if (typeof patch === 'string') {
        setIssue({ lineId: cur.lineId, problem: patch })
        setEditNow({ ...cur, details: true })
        return false
      }
      if (patch === null) return true
      try {
        const saved = await patchLine(dramaId, line.id, patch)
        replaceLine(saved)
        // Keep typing that happened during the save: only the base moves on.
        const now = st.current.edit
        if (now && now.lineId === saved.id) setEditNow({ ...now, base: saved })
        setIssue(null)
        st.current.onChanged()
        return true
      } catch (e) {
        failLine(cur.lineId, e)
        return false
      }
    }
    const stillDirty = (lineId: number) => {
      const now = st.current.edit
      return !!now && now.lineId === lineId && isDirty(now.base, now.draft)
    }

    // Close the editor, saving a dirty draft first; false keeps it open.
    const leaveEdit = async (): Promise<boolean> => {
      const cur = st.current.edit
      if (!cur) return true
      // Save until nothing is left (text typed during a slow save saves too).
      let ok = true
      for (let i = 0; i < 3 && stillDirty(cur.lineId); i += 1) if (!(await saveEdit())) { ok = false; break }
      if (!ok || stillDirty(cur.lineId)) {
        // The row may be out of view (filter, search, reload): say where the draft is.
        if (!find(cur.lineId)) setStatus(`Save or discard your edit to #${lineNumber(cur.base.idx)} first (see above the list).`)
        return false
      }
      setEditNow(null)
      return true
    }

    const focusTo = (id: number) => {
      focusActive.current = id
      setActiveId(id)
    }

    const activate = async (id: number, focus = false) => {
      const cur = st.current.edit
      if (cur && cur.lineId !== id && !(await leaveEdit())) return
      if (focus) focusTo(id)
      else setActiveId(id)
    }

    const openEdit = async (id: number, details = false): Promise<boolean> => {
      const cur = st.current.edit
      if (cur && cur.lineId === id) {
        if (details && !cur.details) setEditNow({ ...cur, details: true })
        setActiveId(id)
        return true
      }
      if (!(await leaveEdit())) return false
      const line = find(id)
      if (!line) return false
      setActiveId(id)
      setIssue(null)
      setEditNow({ lineId: id, base: line, draft: draftFromLine(line), details, note: null })
      return true
    }

    const goPage = async (p: number, target: Target) => {
      if (!(await leaveEdit())) return
      pending.current = { target }
      setPage(p)
    }

    const move = (delta: 1 | -1) => {
      const { shown: lines, activeId: cur, page: pg, pages: n, searching: s } = st.current
      const step = stepFrom(lines, cur, delta, { next: !s && pg < n, prev: !s && pg > 1 })
      if (!step) return
      if ('id' in step) void activate(step.id, true)
      else void goPage(pg + delta, delta > 0 ? 'first' : 'last')
    }

    // Previous/next flagged line (Alt+Up/Down and the buttons): on this page
    // first, then the server finds the nearest one on any page (R08). In a
    // filtered view the server is asked from the focused row (flaggedStep).
    const moveFlagged = async (delta: 1 | -1) => {
      const { shown: lines, page: pg, searching: s, filter: f, activeId: cur } = st.current
      const row = (document.activeElement as HTMLElement | null)?.closest?.('[data-line-id]')
      const from = row
        ? lines.findIndex((l) => String(l.id) === row.getAttribute('data-line-id'))
        : lines.findIndex((l) => l.id === cur)
      if (s) {
        const id = nextFlaggedId(lines, from, delta)
        if (id !== null) void activate(id, true)
        else setStatus('No more flagged lines in these results.')
        return
      }
      const step = flaggedStep(lines, f, from, delta)
      if ('id' in step) {
        void activate(step.id, true)
        return
      }
      try {
        const r = await flaggedAdjacent(dramaId, delta > 0 ? 'next' : 'prev', step.fromId, PAGE_SIZE, f)
        if (r.line_id === null || r.page_all === null) setStatus('No more flagged lines.')
        else if (r.page === pg) void activate(r.line_id, true)
        else if (r.page !== null) void goPage(r.page, r.line_id)
        else {
          // The filter hides it: show every line to open it.
          if (!(await leaveEdit())) return
          pending.current = { target: r.line_id }
          setStatus('Showing all lines to open the flagged line.')
          setFilter('all')
          setPage(r.page_all)
        }
      } catch (e) {
        setError(e)
      }
    }

    const saveAndNext = async () => {
      const cur = st.current.edit
      if (!cur || !(await saveEdit())) return
      // Typed during the save: save that too before moving on (or stay).
      if (stillDirty(cur.lineId) && !(await saveEdit())) return
      if (stillDirty(cur.lineId)) return
      const { shown: lines, page: pg, pages: n, searching: s } = st.current
      const i = lines.findIndex((l) => l.id === cur.lineId)
      const next = lines[i + 1]
      if (next) {
        setActiveId(next.id)
        setIssue(null)
        setEditNow({ lineId: next.id, base: next, draft: draftFromLine(next), details: false, note: null })
      } else if (!s && pg < n) {
        setEditNow(null)
        pending.current = { target: 'first', edit: true }
        setPage(pg + 1)
      } else {
        setEditNow(null)
        focusTo(cur.lineId)
        setStatus('Saved. That was the last line.')
      }
    }

    const openSheet = async (id: number | null, view: SheetView = 'menu', extra: Partial<SheetState> = {}) => {
      if (id !== null) {
        const cur = st.current.edit
        // Structure edits work on saved text: save (or keep) the draft first.
        if (cur && view !== 'menu' && !(await leaveEdit())) return
        if (cur && cur.lineId !== id && !(await leaveEdit())) return
        setActiveId(id)
      }
      setStructError(null)
      setSheet({ lineId: id, view, ...extra })
    }

    const actions: RowActions = {
      activate: (id) => void activate(id),
      openEdit: (id, details) => void openEdit(id, details),
      setDraft: (patch: Partial<LineDraft>) => {
        const cur = st.current.edit
        if (cur) setEditNow({ ...cur, draft: { ...cur.draft, ...patch } })
      },
      cancelEdit: () => {
        const cur = st.current.edit
        setEditNow(null)
        setIssue((i) => (i?.problem ? null : i))
        if (cur) focusTo(cur.lineId)
      },
      save: () => void saveEdit(),
      saveAndNext: () => void saveAndNext(),
      toggleDetails: (id) => {
        const cur = st.current.edit
        if (cur && cur.lineId === id && cur.details) {
          if (isDirty(cur.base, cur.draft)) setEditNow({ ...cur, details: false, note: null })
          else setEditNow(null)
        } else void openEdit(id, true)
      },
      setNote: (note: NoteDraft | null) => {
        const cur = st.current.edit
        if (cur) setEditNow({ ...cur, note })
      },
      saveNote: () => {
        const cur = st.current.edit
        if (!cur?.note) return
        const { term: t, type, text } = cur.note
        addNote(dramaId, { line_id: cur.lineId, term: t, note_type: type, note: text }).then(() => {
          const now = st.current.edit
          if (now) setEditNow({ ...now, note: null })
          setIssue(null)
          setStatus('Note saved.')
          st.current.onChanged()
        }, (e) => failLine(cur.lineId, e))
      },
      openSheet: (id) => void openSheet(id),
      openStructure: (id, view) => void openSheet(id, view),
      splitAtCursor: (id, field, offset) => {
        const cur = st.current.edit
        const line = find(id)
        if (!line) return
        const zh = cur?.lineId === id ? cur.draft.zh : line.zh
        const en = cur?.lineId === id ? cur.draft.en : line.en
        let at: number
        let enAt: number | null = null
        if (field === 'zh') at = codePointOffset(zh, offset)
        else {
          enAt = codePointOffset(en, offset)
          const enLen = charCount(en)
          at = enLen > 0 ? Math.round((charCount(zh) * enAt) / enLen) : Math.round(charCount(zh) / 2)
          if (enAt <= 0 || enAt >= enLen) enAt = null
        }
        void openSheet(id, 'split', { splitAt: at, splitEnAt: enAt })
      },
      setAi: (id, mode) => {
        setActiveId(id)
        setAi(mode ? { lineId: id, mode } : null)
      },
      useSuggestion: async (id, text) => {
        const line = find(id)
        if (!line) return false
        const patch = suggestionPatch(line, text)
        if (!patch) return true
        try {
          const saved = await patchLine(dramaId, id, patch)
          replaceLine(saved)
          setIssue(null)
          st.current.onChanged()
          return true
        } catch (e) {
          failLine(id, e)
          return false
        }
      },
      dismissFlag: async (id) => {
        // Save any draft first: under the Flagged filter the line leaves the view.
        if (!(await leaveEdit())) return
        dismissFlag(dramaId, id).then((saved) => {
          replaceLine(saved)
          setIssue(null)
          st.current.onChanged()
        }, (e) => failLine(id, e))
      },
      // A line the server already saved (blocked-line retry): show it without
      // a reload. closeEdit ends a clean edit of that line, whose base is now stale.
      applyLine: (saved, closeEdit) => {
        if (closeEdit && st.current.edit?.lineId === saved.id) setEditNow(null)
        replaceLine(saved)
        setIssue(null)
        st.current.onChanged()
      },
      playLine: (line) => player.current?.playLine(line),
      acceptTm: (id, entryId, expectedEn) => {
        acceptTmSuggestion(dramaId, id, entryId, expectedEn).then((saved) => {
          // A clean edit of this line now has a stale base: close it.
          if (st.current.edit?.lineId === id && !stillDirty(id)) setEditNow(null)
          replaceLine(saved)
          setIssue(null)
          st.current.onChanged()
        }, (e) => failLine(id, e))
      },
      dismissTm: (s) => dismissTmEverywhere(dramaId, s),
      clearIssue: () => setIssue(null),
      showOnPage: (id) => void showOnPageRef.current(id),
      reload: () => {
        setEditNow(null)
        setIssue(null)
        st.current.onChanged()
      },
    }

    return { actions, move, moveFlagged, goPage, leaveEdit, openEdit, openSheet, focusTo, saveEdit, stillDirty, setEditNow }
  }, [dramaId])

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

  // A dirty draft is never lost silently: leaving the page asks first, and
  // leaving the stage (unmount) saves it.
  const dirtyNow = edit !== null && isDirty(edit.base, edit.draft)
  useEffect(() => {
    if (!dirtyNow) return
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirtyNow])
  useEffect(
    () => () => {
      const cur = st.current.edit
      if (cur && ctl.stillDirty(cur.lineId)) void ctl.saveEdit()
    },
    [ctl],
  )

  // ---- structure edits (sheet) ----
  const runStructure = async (
    call: (ids: number[]) => Promise<RestructureResult>,
    after: (r: RestructureResult, ids: number[]) => { id: number | null; message: string },
  ) => {
    // Claimed synchronously, before any await, so a double click sends one edit.
    if (busyRef.current) return
    busyRef.current = true
    setBusy(true)
    setStructError(null)
    setSheetNote(null)
    // Never drop a draft: save it (or stop) before the lines change shape.
    if (!(await ctl.leaveEdit())) {
      setSheetNote(DRAFT_NOT_SAVED)
      busyRef.current = false
      busyRef.current = false
      setBusy(false)
      return
    }
    try {
      const ids = (await listAllLines(dramaId)).map((l) => l.id)
      if (!pageStillMatches(ids, shown.map((l) => l.id), searching ? 'search' : filter, page)) throw mismatch()
      const r = await call(ids)
      const { id, message } = after(r, ids)
      setSheet(null)
      setEdit(null)
      setAi(null)
      if (id !== null) {
        pending.current = { target: id }
        if (!searching && filter === 'all') {
          const pos = r.line_ids.indexOf(id)
          if (pos !== -1) setPage(pageForPosition(pos))
        }
      }
      setStatus(message)
      onChanged()
    } catch (e) {
      setStructError(e)
    } finally {
      busyRef.current = false
      setBusy(false)
    }
  }

  const UNDO = ' Undo in Records → Line history.'
  const sheetLine = sheet ? (shown.find((l) => l.id === sheet.lineId) ?? null) : null
  const sheetRun = sheetLine ? shown.slice(shown.findIndex((l) => l.id === sheetLine.id)) : []

  const doSplit = (c: SplitChoice) => {
    const line = sheetLine
    if (!line) return
    void runStructure(
      (ids) => splitLine(dramaId, line.id, { expected_line_ids: ids, at_char: c.at_char, expected_zh: line.zh, at_time: c.at_time, en_at_char: c.en_at_char }),
      (r) => {
        const [a, b] = r.lines
        return { id: b?.id ?? a?.id ?? null, message: a && b ? `Split #${lineNumber(a.idx)} into #${lineNumber(a.idx)}–#${lineNumber(b.idx)}.${UNDO}` : `Line split.${UNDO}` }
      },
    )
  }
  const doMerge = (lineIds: number[]) => {
    const chosen = shown.filter((l) => lineIds.includes(l.id))
    void runStructure(
      (ids) => {
        const run = adjacentRun(ids, lineIds[0], lineIds.length)
        if (!run || run.some((id, i) => id !== lineIds[i])) throw mismatch()
        return mergeLines(dramaId, lineIds, ids)
      },
      (r) => {
        const head = r.lines[0]
        return { id: head?.id ?? lineIds[0], message: `Merged ${lineRange(chosen)}${head ? ` into #${lineNumber(head.idx)}` : ''}.${UNDO}` }
      },
    )
  }
  const doAdd = (nl: NewLine) => {
    const after = sheetLine
    void runStructure(
      (ids) => addLine(dramaId, { expected_line_ids: ids, after_line_id: after?.id ?? null, ...nl }),
      (r) => ({ id: r.lines[0]?.id ?? null, message: r.lines[0] ? `Added line #${lineNumber(r.lines[0].idx)}.` : 'Line added.' }),
    )
  }
  const doDelete = () => {
    const line = sheetLine
    if (!line) return
    void runStructure(
      (ids) => deleteLine(dramaId, line.id, ids),
      (r, before) => {
        const pos = before.indexOf(line.id)
        const id = r.line_ids[pos] ?? r.line_ids[pos - 1] ?? null
        return { id, message: `Deleted #${lineNumber(line.idx)}.${UNDO}` }
      },
    )
  }
  // Writes only `lang`, so unlike the structure edits it needs no line-list check.
  const doSetLanguage = async (lang: string, scope: LanguageScope) => {
    const line = sheetLine
    if (!line || busyRef.current) return
    busyRef.current = true
    setBusy(true)
    setStructError(null)
    setSheetNote(null)
    try {
      const target = scope === 'speaker' && line.speaker ? { speaker: line.speaker } : { line_ids: [line.id] }
      const r = await setLinesLanguage(dramaId, { lang: lang || null, ...target })
      setSheet(null)
      pending.current = { target: line.id }
      setStatus(languageSetText(r.updated, lang, sourceLanguage))
      onChanged()
    } catch (e) {
      setStructError(e)
    } finally {
      busyRef.current = false
      setBusy(false)
    }
  }
  const closeSheetThen = (fn: () => void) => {
    setSheet(null)
    fn()
  }

  // ---- toolbar ----
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

  // Translation-memory suggestions for the lines shown (R11). Optional: a
  // failure (or no permission) just shows none.
  const shownIds = shown.map((l) => l.id).join(',')
  useEffect(() => {
    let cancelled = false
    const ids = shownIds ? shownIds.split(',').map(Number).slice(0, 200) : []
    // Nothing shown: the old list matches no row, so it can stay.
    if (ids.length === 0) return
    listTmSuggestions(dramaId, undefined, ids).then(
      (list) => !cancelled && setTmList(list),
      () => !cancelled && setTmList([]),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, shownIds, reloads])
  // Lines to offer the stronger engine for (no engine call).
  const strongerByLine = useStrongerOffers(dramaId, reloads)
  const tmByLine = useMemo(() => {
    const m = new Map<number, TmSuggestion>()
    for (const s of visibleTm(tmList, tmDismissed)) if (s.line_id !== null) m.set(s.line_id, s)
    return m
  }, [tmList, tmDismissed])

  // ---- shortcuts ----
  const active = activeId !== null ? shown.find((l) => l.id === activeId) ?? null : null
  useShortcut((combo, { inText, event }) => {
    if (sheet || keysOpen) return false
    if (combo === 'alt+ ') {
      if (!mediaKind) return false
      player.current?.togglePlay()
      return true
    }
    if (inText) return false
    const target = event.target as HTMLElement
    const inList = target === document.body || !!listRef.current?.contains(target)
    const onRow = target === document.body || target.matches?.('.review-line')
    const isControl = !!target.closest?.('input, select')
    // Single-key shortcuts only while focus is in the list (or nowhere).
    if ((combo.length === 1 && combo !== '?' && combo !== '/') || combo === 'shift+delete') {
      if (!inList) return false
    }
    switch (combo) {
      case 'arrowdown':
      case 'arrowup':
        if (!inList || isControl) return false
        ctl.move(combo === 'arrowdown' ? 1 : -1)
        return true
      case 'j':
      case 'k':
        ctl.move(combo === 'j' ? 1 : -1)
        return true
      case 'alt+arrowdown':
      case 'alt+arrowup':
        void ctl.moveFlagged(combo === 'alt+arrowdown' ? 1 : -1)
        return true
      case ']':
      case '[': {
        const p = page + (combo === ']' ? 1 : -1)
        if (searching || p < 1 || p > pages) return false
        void ctl.goPage(p, 'first')
        return true
      }
      case '/':
        if (isPhone && !searchRef.current) return false
        searchRef.current?.focus()
        return true
      case '?':
        setKeysOpen(true)
        return true
    }
    if (!active) return false
    switch (combo) {
      case 'enter':
        if (!onRow) return false
        void ctl.openEdit(active.id)
        return true
      case 'e':
        void ctl.openEdit(active.id)
        return true
      case 'd':
        actions.toggleDetails(active.id)
        return true
      case ' ':
        if (!mediaKind || !onRow) return false
        player.current?.toggleLine(active)
        return true
      case 'l':
        if (!mediaKind) return false
        player.current?.toggleLoop()
        return true
      case 'm':
      case 'a':
        if (limited) setStatus(ALL_LINES_ONLY)
        else void ctl.openSheet(active.id, combo === 'm' ? 'merge' : 'add')
        return true
      case 'shift+delete':
        void ctl.openSheet(active.id, 'menu', { armDelete: true })
        return true
      case 'f':
        if (!active.flag) return false
        actions.dismissFlag(active.id)
        return true
      case 'i':
        if (!active.en) return false
        actions.setAi(active.id, 'improve')
        return true
      case 'w':
        actions.setAi(active.id, 'explain')
        return true
      case 'escape':
        if (!ai) return false
        setAi(null)
        return true
    }
    return false
  })

  const counts = data
    ? { all: allTotal, flagged: data.flagged_count, untranslated: data.untranslated_count }
    : null
  const loading = !searching && data === null && !error
  const showPager = !searching && !!data && pages > 1
  const editingActive = edit !== null && edit.lineId === activeId
  // Phones: the pager shares the player's row so the sticky toolbar stays short.
  const pagerInPlayer = isPhone && mediaKind !== null && showPager
  const sideVideo = isWide && !isPhone && mediaKind === 'video' && !emptyDrama

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
            ) : null
          }
        />
        )}
        {isPhone && mediaKind && !emptyDrama && <div className="review-player-dock" ref={setPlayerDock} />}
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
        <ErrorBanner error={error} onDismiss={() => setError(null)} />
        {hiddenEdit && (
          <div className="banner review-hidden-edit" role="alert" data-testid="hidden-edit">
            <span>
              Your edit to #{lineNumber(hiddenEdit.base.idx)} is outside this view.
              {issue?.lineId === hiddenEdit.lineId && issue.conflict && ' It changed elsewhere, so it could not be saved.'}
            </span>
            <span className="actions">
              <button type="button" className={buttonClass('primary', 'sm')} onClick={() => void ctl.saveEdit().then((ok) => ok && ctl.setEditNow(null))}>Save</button>
              <button type="button" className={buttonClass('secondary', 'sm')} onClick={() => void showHidden()}>Show</button>
              <button type="button" className={buttonClass('ghost', 'sm')} onClick={discardHidden}>Discard</button>
            </span>
          </div>
        )}

        {loading && (
          <ul className="review-lines" aria-hidden="true">
            {Array.from({ length: 6 }, (_, i) => (
              <li key={i} className="review-line review-skeleton" />
            ))}
          </ul>
        )}
        {!loading && shown.length === 0 && !error && (
          <div className="review-empty">
            <p className="muted">{emptyMessage(filter, term)}</p>
            {filter === 'all' && !searching ? (
              <div className="actions">
                <ButtonLink variant="primary" href={routeHref({ name: 'drama', id: dramaId, stage: 'source' })}>
                  Go to Source
                </ButtonLink>
                <button type="button" className={buttonClass('secondary')} disabled={jobRunning} onClick={() => void ctl.openSheet(null, 'add')}>
                  Add first line
                </button>
              </div>
            ) : (
              <button type="button" className={buttonClass('ghost')} onClick={() => void changeFilter('all')}>
                All lines
              </button>
            )}
          </div>
        )}
        <ul className={compact ? 'review-lines is-compact' : 'review-lines'} ref={listRef}>
          {shown.map((l) => (
            <LineRow
              key={l.id}
              dramaId={dramaId}
              line={l}
              sourceLanguage={sourceLanguage}
              active={l.id === activeId}
              isPhone={isPhone}
              hasMedia={mediaKind !== null}
              jobRunning={jobRunning}
              limited={limited}
              edit={edit?.lineId === l.id ? edit : null}
              ai={ai?.lineId === l.id ? ai.mode : null}
              tm={tmByLine.get(l.id) ?? null}
              stronger={strongerByLine.get(l.id) ?? null}
              issue={issue?.lineId === l.id ? issue : null}
              actions={actions}
              searchHit={searching}
              jumped={jumpedId === l.id}
            />
          ))}
        </ul>
        {showPager && (
          <div className="review-bottom-pager">
            <Pager page={page} pages={pages} onPage={(p) => void ctl.goPage(p, 'first')} />
          </div>
        )}

        {isPhone && active && (
          <div className="review-editbar" role="toolbar" aria-label="Line actions">
            {editingActive ? (
              <>
                <button type="button" className={buttonClass('ghost')} onClick={actions.cancelEdit}>Cancel</button>
                <button type="button" className={buttonClass('secondary')} onClick={actions.save}>Save</button>
                <button type="button" className={buttonClass('primary')} onClick={actions.saveAndNext}>Save &amp; next</button>
              </>
            ) : (
              <>
                {mediaKind && (
                  <button
                    type="button"
                    aria-label={`Play line ${lineNumber(active.idx)}`}
                    onClick={() => {
                      showActive()
                      player.current?.toggleLine(active)
                    }}
                  >
                    ▶ #{lineNumber(active.idx)}
                  </button>
                )}
                <button type="button" aria-label="Previous line" onClick={() => ctl.move(-1)}>‹ Prev</button>
                <button type="button" aria-label="Next line" onClick={() => ctl.move(1)}>Next ›</button>
                <button
                  type="button"
                  onClick={() => {
                    showActive()
                    void ctl.openEdit(active.id)
                  }}
                >
                  Edit #{lineNumber(active.idx)}
                </button>
              </>
            )}
          </div>
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
          onPlay={() => closeSheetThen(() => sheetLine && player.current?.playLine(sheetLine))}
          onEditDetails={() => closeSheetThen(() => sheetLine && void ctl.openEdit(sheetLine.id, true))}
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
          onSplit={doSplit}
          onMerge={doMerge}
          onAdd={doAdd}
          onDelete={doDelete}
          sourceLanguage={sourceLanguage}
          onSetLanguage={(lang, scope) => void doSetLanguage(lang, scope)}
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
