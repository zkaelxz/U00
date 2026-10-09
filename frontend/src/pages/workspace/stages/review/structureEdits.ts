import type { Dispatch, MutableRefObject, SetStateAction } from 'react'

import { ApiError } from '../../../../api/client'
import { addLine, deleteLine, listAllLines, mergeLines, restoreSnapshot, splitLine } from '../../../../api/restructure'
import { setLinesLanguage } from '../../../../api/review'
import { lineNumber } from '../../../../lineNumber'
import type { RestructureResult } from '../../../../types/restructure'
import type { LineFilter, ReviewLine } from '../../../../types/review'
import type { NewLine } from './AddLineForm'
import type { SheetState } from './LineActionsSheet'
import type { EditState } from './LineRow'
import type { Pending } from './linesController'
import type { LineSelection } from './useLineSelection'
import { languageSetText, type LanguageScope } from './reviewDraft'
import { adjacentRun, lineRange, pageForPosition, pageStillMatches, type PanelMode, undoDoneMessage, undoHandleOf, undoRefusal, type UndoHandle, type UndoKind } from './reviewLogic'
import type { SplitChoice } from './SplitDialog'
import { retireUndoOffer } from './undoOffer'

// Shown when the server did not return what an undo needs (an older server).
const RECORDS_UNDO = ' Undo in Records → Line history.'
export const DRAFT_NOT_SAVED = 'Your edit to this line could not be saved, so nothing else was changed. Close this and check the line.'

const mismatch = () => new ApiError(409, { code: 'conflict', message: 'lines changed' })

export type UndoOffer = { handle: UndoHandle; message: string; kind: UndoKind; at: number }

export interface StructureEditsDeps {
  dramaId: number
  shown: ReviewLine[]
  sheetLine: ReviewLine | null
  searching: boolean
  filter: LineFilter
  page: number
  activeId: number | null
  sourceLanguage: string | null
  undo: UndoOffer | null
  selection: LineSelection
  pending: MutableRefObject<Pending | null>
  busyRef: MutableRefObject<boolean>
  leaveEdit: () => Promise<boolean>
  focusTo: (id: number) => void
  setBusy: Dispatch<SetStateAction<boolean>>
  setStructError: Dispatch<SetStateAction<unknown>>
  setSheetNote: Dispatch<SetStateAction<string | null>>
  setSheet: Dispatch<SetStateAction<SheetState | null>>
  setEdit: Dispatch<SetStateAction<EditState | null>>
  setAi: Dispatch<SetStateAction<{ lineId: number; mode: PanelMode } | null>>
  setPage: Dispatch<SetStateAction<number>>
  setStatus: Dispatch<SetStateAction<string | null>>
  setUndo: (offer: UndoOffer | null) => void
  onChanged: () => void
}

// The "⋯" sheet's structure edits (split, merge, add, delete, language) and the
// undo of the last one. Rebuilt every render so it reads the current view.
export function buildStructureEdits(deps: StructureEditsDeps) {
  const {
    dramaId, shown, sheetLine, searching, filter, page, activeId, sourceLanguage, undo, selection,
    pending, busyRef, setBusy, setStructError, setSheetNote, setSheet, setEdit, setAi, setPage,
    setStatus, setUndo, onChanged,
  } = deps
  const ctl = { leaveEdit: deps.leaveEdit, focusTo: deps.focusTo }

  const runStructure = async (
    call: (ids: number[]) => Promise<RestructureResult>,
    after: (r: RestructureResult, ids: number[]) => { id: number | null; message: string; undo?: { kind: UndoKind; at: number } },
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
      const { id, message, undo: undoable } = after(r, ids)
      const handle = undoable ? undoHandleOf(r) : null
      setSheet(null)
      setEdit(null)
      setAi(null)
      // Line numbers shift and ids may vanish, so the ticks no longer mean what they did.
      selection.clear()
      if (id !== null) {
        pending.current = { target: id }
        if (!searching && filter === 'all') {
          const pos = r.line_ids.indexOf(id)
          if (pos !== -1) setPage(pageForPosition(pos))
        }
      }
      if (handle && undoable) setUndo({ handle, message, ...undoable })
      else retireUndoOffer()
      setStatus(handle ? null : message + (undoable ? RECORDS_UNDO : ''))
      onChanged()
    } catch (e) {
      setStructError(e)
    } finally {
      busyRef.current = false
      setBusy(false)
    }
  }

  const doUndo = async () => {
    if (!undo || busyRef.current) return
    busyRef.current = true
    setBusy(true)
    setStructError(null)
    try {
      // Saves an open draft first; a saved edit changes the lines, so the server then
      // refuses the undo rather than overwrite it.
      if (!(await ctl.leaveEdit())) return
      const ids = (await listAllLines(dramaId)).map((l) => l.id)
      const r = await restoreSnapshot(dramaId, undo.handle.historyId, ids, undo.handle.fingerprint)
      setUndo(null)
      setSheet(null)
      setEdit(null)
      selection.clear()
      // The notice (and the Undo button that had focus) goes away: focus the restored line.
      const back = r.line_ids[undo.at] ?? r.line_ids[r.line_ids.length - 1]
      if (back !== undefined) {
        pending.current = { target: back }
        if (!searching && filter === 'all') setPage(pageForPosition(r.line_ids.indexOf(back)))
      }
      setStatus(undoDoneMessage(undo.kind))
      onChanged()
    } catch (e) {
      const refused = undoRefusal(e)
      if (refused) {
        if (!refused.keepOffer) {
          setUndo(null)
          // Focus was on the Undo button, which goes away with the notice.
          if (activeId !== null) ctl.focusTo(activeId)
        }
        setStatus(refused.text)
      } else setStructError(e)
    } finally {
      busyRef.current = false
      setBusy(false)
    }
  }

  const doSplit = (c: SplitChoice) => {
    const line = sheetLine
    if (!line) return
    void runStructure(
      (ids) => splitLine(dramaId, line.id, { expected_line_ids: ids, at_char: c.at_char, expected_zh: line.zh, at_time: c.at_time, en_at_char: c.en_at_char }),
      (r, before) => {
        const [a, b] = r.lines
        return {
          id: b?.id ?? a?.id ?? null,
          message: a && b ? `Split #${lineNumber(a.idx)} into #${lineNumber(a.idx)}–#${lineNumber(b.idx)}.` : 'Line split.',
          undo: { kind: 'split', at: before.indexOf(line.id) },
        }
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
      (r, before) => {
        const head = r.lines[0]
        return {
          id: head?.id ?? lineIds[0],
          message: `Merged ${lineRange(chosen)}${head ? ` into #${lineNumber(head.idx)}` : ''}.`,
          undo: { kind: 'merge', at: before.indexOf(lineIds[0]) },
        }
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
        return { id, message: `Deleted #${lineNumber(line.idx)}.`, undo: { kind: 'delete', at: pos } }
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
  return { doUndo, doSplit, doMerge, doAdd, doDelete, doSetLanguage }
}
