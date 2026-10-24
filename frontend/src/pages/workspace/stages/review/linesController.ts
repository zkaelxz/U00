import type { Dispatch, MutableRefObject, RefObject, SetStateAction } from 'react'

import { hasStatus } from '../../../../api/client'
import { applyFindReplace, patchLine } from '../../../../api/review'
import { listAllLines } from '../../../../api/restructure'
import { lineNumber } from '../../../../lineNumber'
import type { ApplyResult, LineFilter, ReviewLine, ReviewLinesPage, ReviewMatch } from '../../../../types/review'
import { buildRowActions } from './lineRowActions'
import { createMoveFlagged } from './lineFlaggedMove'
import { createTiming } from './lineTiming'
import type { SheetState, SheetView } from './LineActionsSheet'
import type { EditState, RowIssue } from './LineRow'
import type { PlayerHandle } from './Player'
import { buildPatch, clashingFields, draftFromLine, isDirty, rebaseDraft, resetFieldsText, type LineDraft } from './reviewDraft'
import { inFilter, stepFrom, type PanelMode } from './reviewLogic'
import { retireUndoOffer } from './undoOffer'

export type Target = 'first' | 'last' | number
export type Pending = { target: Target; edit?: boolean }
// The lines a write changes: some ids, or every line (a version, a restore).
export type LineScope = number[] | 'all'

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

// What the extracted action builders (lineRowActions, lineTiming, lineFlaggedMove) need from the
// controller's own helpers.
export interface LinesCore {
  find: (id: number | null) => ReviewLine | null
  replaceLine: (saved: ReviewLine) => void
  failLine: (lineId: number, e: unknown) => void
  setEditNow: (e: EditState | null) => void
  saveEdit: () => Promise<boolean>
  stillDirty: (lineId: number) => boolean
  leaveEdit: () => Promise<boolean>
  beforeWrite: (scope: LineScope) => Promise<string | null>
  afterWrite: (scope: LineScope) => void
  focusTo: (id: number) => void
  activate: (id: number, focus?: boolean) => Promise<void>
  openEdit: (id: number, details?: boolean) => Promise<boolean>
  openSheet: (id: number | null, view?: SheetView, extra?: Partial<SheetState>) => Promise<void>
  saveAndNext: () => Promise<void>
  goPage: (p: number, target: Target) => Promise<void>
}

// Every write to a line goes through here, so a dirty draft is saved (or kept,
// if the save fails) before the editor moves on. Built once per drama; the
// mutable state it reads lives behind `st` and the refs.
export function createLinesController(deps: LinesControllerDeps) {
  const {
    dramaId, st, pending, focusActive,
    setData, setFound, setIssue, setEdit, setStatus, setActiveId, setPage,
    setSheet, setStructError,
  } = deps
  // The line whose last save failed, until it is saved or its draft changes.
  const failed: { current: number | null } = { current: null }
  const find = (id: number | null) => st.current.shown.find((l) => l.id === id) ?? null
  const replaceLine = (saved: ReviewLine) => {
    // A saved edit changes what any undo would overwrite.
    retireUndoOffer()
    setData((d) => (d ? { ...d, lines: d.lines.map((l) => (l.id === saved.id ? saved : l)) } : d))
    setFound((f) => (f ? f.map((l) => (l.id === saved.id ? saved : l)) : f))
    st.current.shown = st.current.shown.map((l) => (l.id === saved.id ? saved : l))
  }
  const failLine = (lineId: number, e: unknown) => {
    failed.current = lineId
    if (hasStatus(e, 409)) setIssue({ lineId, conflict: true })
    else setIssue({ lineId, error: e })
  }
  const setEditNow = (e: EditState | null) => {
    const prev = st.current.edit
    // New typing, a new base or a Reload may make the draft saveable again.
    if (!e || e.draft !== prev?.draft || e.base !== prev?.base || e.rebaseOnLoad) failed.current = null
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
      failed.current = cur.lineId
      setIssue({ lineId: cur.lineId, problem: patch })
      setEditNow({ ...cur, details: true })
      return false
    }
    if (patch === null) return true
    try {
      const saved = await patchLine(dramaId, line.id, patch)
      replaceLine(saved)
      // Keep what was typed, including during the save; fields left alone follow
      // the saved line, or the next save would send their old text back over a
      // change made meanwhile (a running job, another device) with `expected` set to it.
      const now = st.current.edit
      if (now && now.lineId === saved.id) setEditNow({ ...now, base: saved, draft: rebaseDraft(line, saved, now.draft) })
      setIssue(null)
      if (failed.current === saved.id) failed.current = null
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
  const editIn = (scope: LineScope) => {
    const cur = st.current.edit
    return cur && (scope === 'all' || scope.includes(cur.lineId)) ? cur : null
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

  // A write that replaces lines under an open edit would leave the edit on a
  // stale base, so its next save gets a 409 whose only way out (Reload)
  // throws the typing away. Save and close that edit first; a message when
  // it can't be saved, so the write waits.
  const beforeWrite = async (scope: LineScope): Promise<string | null> => {
    const cur = editIn(scope)
    if (!cur || (await leaveEdit())) return null
    return `Save or discard your edit to #${lineNumber(cur.base.idx)} first.`
  }
  // An edit opened on those lines while the write ran started from the old
  // text: close it when nothing was typed.
  const afterWrite = (scope: LineScope) => {
    const cur = editIn(scope)
    if (cur && !isDirty(cur.base, cur.draft)) setEditNow(null)
  }

  // A draft kept from an earlier visit (useDraftGuard), rebased on the line as
  // it is now. Never replaces an edit already open.
  const restoreDraft = (base: ReviewLine, draft: LineDraft): boolean => {
    if (st.current.edit) return false
    setActiveId(base.id)
    setIssue(null)
    setEditNow({ lineId: base.id, base, draft, details: false, note: null })
    setStatus(`Your unsaved edit to #${lineNumber(base.idx)} is back. Save or discard it.`)
    return true
  }

  // After Reload on a conflict: the kept draft moves onto the line as it is now.
  // A field changed elsewhere shows that change, and the status names it
  // (saving the typing over it unseen would lose it); other typing is kept.
  // A line the reloaded view doesn't list is read from the whole title.
  const rebaseKept = async (lines: ReviewLine[]) => {
    const asked = st.current.edit
    if (!asked?.rebaseOnLoad) return
    let fresh = lines.find((l) => l.id === asked.lineId)
    if (!fresh) {
      try {
        fresh = (await listAllLines(dramaId)).find((l) => l.id === asked.lineId)
      } catch {
        // Unreadable now: the next save says why.
      }
    }
    const cur = st.current.edit
    // A save or an earlier rebase moved the base on meanwhile: this list may be older.
    if (!cur || cur.lineId !== asked.lineId || cur.base !== asked.base || !cur.rebaseOnLoad) return
    if (!fresh) {
      setEditNow({ ...cur, rebaseOnLoad: false })
      return
    }
    const reset = clashingFields(cur.base, fresh, cur.draft)
    const draft = rebaseDraft(cur.base, fresh, cur.draft)
    const now = draftFromLine(fresh)
    for (const k of reset) (draft as unknown as Record<string, unknown>)[k] = now[k]
    setEditNow({ ...cur, base: fresh, draft, rebaseOnLoad: false })
    if (reset.length) setStatus(resetFieldsText(reset, lineNumber(fresh.idx)))
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

  const saveAndNext = async () => {
    const cur = st.current.edit
    if (!cur || !(await saveEdit())) return
    // Typed during the save: save that too before moving on (or stay).
    if (stillDirty(cur.lineId) && !(await saveEdit())) return
    if (stillDirty(cur.lineId)) return
    const { shown: lines, page: pg, pages: n, searching: s, filter } = st.current
    const i = lines.findIndex((l) => l.id === cur.lineId)
    const next = lines[i + 1]
    const saved = st.current.edit?.base
    if (next) {
      setActiveId(next.id)
      setIssue(null)
      setEditNow({ lineId: next.id, base: next, draft: draftFromLine(next), details: false, note: null })
    } else if (!s && pg < n && saved && !inFilter(saved, filter)) {
      // The saved line drops out of the filter on the reload the save started, so
      // the next page's first line moves up into this page's last place.
      setEditNow(null)
      pending.current = { target: 'last', edit: true }
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


  const core: LinesCore = {
    find, replaceLine, failLine, setEditNow, saveEdit, stillDirty, leaveEdit, beforeWrite, afterWrite,
    focusTo, activate, openEdit, openSheet, saveAndNext, goPage,
  }
  const actions = buildRowActions(deps, core)
  const moveFlagged = createMoveFlagged(deps, core)
  const { setTiming, retime } = createTiming(deps, core)

  return {
    dramaId, actions, retime, setTiming, move, moveFlagged, goPage, leaveEdit, openEdit, openSheet, focusTo,
    saveEdit, stillDirty, setEditNow, beforeWrite, afterWrite, restoreDraft, setStatus,
    saveFailed: (lineId: number) => failed.current === lineId,
    rebaseKept,
  }
}

export type LinesController = ReturnType<typeof createLinesController>

// Records (Use this version, Restore, TM Accept) sits outside the lines panel
// but writes the same lines, so it reaches the open edit through here.
const writers = new Map<number, LinesController>()

export function registerLinesWriter(ctl: LinesController): () => void {
  writers.set(ctl.dramaId, ctl)
  return () => {
    if (writers.get(ctl.dramaId) === ctl) writers.delete(ctl.dramaId)
  }
}

export const beforeLinesWrite = (dramaId: number, scope: LineScope): Promise<string | null> =>
  writers.get(dramaId)?.beforeWrite(scope) ?? Promise.resolve(null)

export const afterLinesWrite = (dramaId: number, scope: LineScope): void => writers.get(dramaId)?.afterWrite(scope)

export const linesLoaded = (dramaId: number, lines: ReviewLine[]): void => void writers.get(dramaId)?.rebaseKept(lines)

/** Find-and-replace Apply, after saving an open edit of a matched line (it would be
 *  left on its old text). A string says why nothing was applied. */
export async function applyAfterEdit(
  dramaId: number, matches: ReviewMatch[], apply = applyFindReplace,
): Promise<ApplyResult | string> {
  const ids = matches.map((m) => m.id)
  const blocked = await beforeLinesWrite(dramaId, ids)
  if (blocked) return blocked
  const r = await apply(dramaId, matches)
  afterLinesWrite(dramaId, ids)
  return r
}
