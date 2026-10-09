import type { Dispatch, MutableRefObject, RefObject, SetStateAction } from 'react'

import { ApiError } from '../../../../api/client'
import { acceptTm as acceptTmSuggestion, addNote, dismissFlag, flaggedAdjacent, patchLine } from '../../../../api/review'
import { lineNumber } from '../../../../lineNumber'
import type { LineFilter, ReviewLine, ReviewLinesPage } from '../../../../types/review'
import type { SheetState, SheetView } from './LineActionsSheet'
import type { EditState, NoteDraft, RowActions, RowIssue } from './LineRow'
import type { PlayerHandle } from './Player'
import { buildPatch, draftFromLine, isDirty, timingPatch, type LineDraft, type TimingField } from './reviewDraft'
import { charCount, codePointOffset, flaggedStep, formatTime, nextFlaggedId, PAGE_SIZE, stepFrom, suggestionPatch, type PanelMode } from './reviewLogic'
import { dismissTmEverywhere } from './tmDismiss'
import { retireUndoOffer } from './undoOffer'
import type { Edge } from './Waveform'

export type Target = 'first' | 'last' | number
export type Pending = { target: Target; edit?: boolean }

// What the stable row callbacks read at call time, kept current by the panel.
export interface LinesState {
  shown: ReviewLine[]
  edit: EditState | null
  page: number
  pages: number
  filter: LineFilter
  searching: boolean
  onChanged: () => void
  jobRunning: boolean
  activeId: number | null
}

export interface LinesControllerDeps {
  dramaId: number
  st: MutableRefObject<LinesState>
  pending: MutableRefObject<Pending | null>
  focusActive: MutableRefObject<number | null>
  player: RefObject<PlayerHandle | null>
  selectRef: MutableRefObject<(id: number, range: boolean) => void>
  showOnPageRef: MutableRefObject<(id: number) => Promise<void>>
  setData: Dispatch<SetStateAction<ReviewLinesPage | null>>
  setFound: Dispatch<SetStateAction<ReviewLine[] | null>>
  setIssue: Dispatch<SetStateAction<RowIssue | null>>
  setEdit: Dispatch<SetStateAction<EditState | null>>
  setStatus: Dispatch<SetStateAction<string | null>>
  setActiveId: Dispatch<SetStateAction<number | null>>
  setPage: Dispatch<SetStateAction<number>>
  setFilter: Dispatch<SetStateAction<LineFilter>>
  setError: Dispatch<SetStateAction<unknown>>
  setSheet: Dispatch<SetStateAction<SheetState | null>>
  setStructError: Dispatch<SetStateAction<unknown>>
  setAi: Dispatch<SetStateAction<{ lineId: number; mode: PanelMode } | null>>
  setRetranscribeFocusId: Dispatch<SetStateAction<number | null>>
}

// Every write to a line goes through here, so a dirty draft is saved (or kept,
// if the save fails) before the editor moves on. Built once per drama; the
// mutable state it reads lives behind `st` and the refs.
export function createLinesController(deps: LinesControllerDeps) {
  const {
    dramaId, st, pending, focusActive, player, selectRef, showOnPageRef,
    setData, setFound, setIssue, setEdit, setStatus, setActiveId, setPage, setFilter,
    setError, setSheet, setStructError, setAi, setRetranscribeFocusId,
  } = deps
  const find = (id: number | null) => st.current.shown.find((l) => l.id === id) ?? null
  const replaceLine = (saved: ReviewLine) => {
    // A saved edit changes what any undo would overwrite.
    retireUndoOffer()
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
    retranscribeFocused: () => setRetranscribeFocusId(null),
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
        // A note on a line the undo would remove makes the server refuse it.
        retireUndoOffer()
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
    select: (id, range) => selectRef.current(id, range),
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

  // Timing hotkeys and waveform drags queue, so each edit starts from the
  // line the previous save returned (a stale base would 409).
  let timingQueue: Promise<unknown> = Promise.resolve()
  const setTiming = (id: number, field: TimingField, value: (line: ReviewLine) => number): Promise<boolean> => {
    const run = timingQueue.then(async () => {
      const line = find(id)
      if (!line) return false
      if (st.current.edit?.lineId === id) {
        setStatus('Save or discard your edit first.')
        return false
      }
      const at = st.current.shown.findIndex((l) => l.id === id)
      const patch = timingPatch(line, { prev: st.current.shown[at - 1], next: st.current.shown[at + 1] }, field, value(line))
      if (typeof patch === 'string') {
        setStatus(patch)
        return false
      }
      if (patch === null) return true
      try {
        const saved = await patchLine(dramaId, id, patch)
        replaceLine(saved)
        setIssue(null)
        setStatus(`#${lineNumber(saved.idx)} ${field} ${formatTime(saved[field])}`)
        st.current.onChanged()
        return true
      } catch (e) {
        failLine(id, e)
        return false
      }
    })
    timingQueue = run
    return run
  }
  const retime = (id: number, edge: Edge, value: number) => setTiming(id, edge, () => value)
  return { actions, retime, setTiming, move, moveFlagged, goPage, leaveEdit, openEdit, openSheet, focusTo, saveEdit, stillDirty, setEditNow }
}
