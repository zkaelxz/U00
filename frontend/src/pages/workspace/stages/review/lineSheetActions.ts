import type { Dispatch, RefObject, SetStateAction } from 'react'

import type { ReviewLine } from '../../../../types/review'
import type { SheetActions, SheetState } from './LineActionsSheet'
import type { createLinesController } from './linesController'
import type { PlayerHandle } from './Player'
import { DRAFT_NOT_SAVED, type buildStructureEdits } from './structureEdits'

interface Deps {
  sheetLine: ReviewLine | null
  ctl: ReturnType<typeof createLinesController>
  edits: ReturnType<typeof buildStructureEdits>
  player: RefObject<PlayerHandle | null>
  setSheet: Dispatch<SetStateAction<SheetState | null>>
  setStructError: Dispatch<SetStateAction<unknown>>
  setSheetNote: Dispatch<SetStateAction<string | null>>
  setRetranscribeFocusId: Dispatch<SetStateAction<number | null>>
}

// What the "⋯" line sheet does for its line. Rebuilt every render, since it reads the open line.
export function buildSheetActions({ sheetLine, ctl, edits, player, setSheet, setStructError, setSheetNote, setRetranscribeFocusId }: Deps): SheetActions {
  const { actions } = ctl
  const closeSheetThen = (fn: () => void) => {
    setSheet(null)
    fn()
  }
  return {
    onView: (view) => {
      setStructError(null)
      setSheetNote(null)
      // Split/merge/add work on saved text: save a draft first, stay on the menu if that fails.
      void (view === 'menu' ? Promise.resolve(true) : ctl.leaveEdit()).then((ok) => {
        if (ok) setSheet((s) => (s ? { ...s, view, armDelete: false } : s))
        else setSheetNote(DRAFT_NOT_SAVED)
      })
    },
    onClose: () => setSheet(null),
    onPlayRange: (start, end) => sheetLine && player.current?.playLine({ id: sheetLine.id, idx: sheetLine.idx, start, end }),
    onPlay: () => closeSheetThen(() => sheetLine && player.current?.playLine(sheetLine)),
    onEditDetails: () => closeSheetThen(() => sheetLine && void ctl.openEdit(sheetLine.id, true)),
    onRetranscribe: () =>
      closeSheetThen(() => {
        if (!sheetLine) return
        const id = sheetLine.id
        void ctl.openEdit(id, true).then((ok) => ok && setRetranscribeFocusId(id))
      }),
    onImprove: () => closeSheetThen(() => sheetLine && actions.setAi(sheetLine.id, 'improve')),
    onWhy: () => closeSheetThen(() => sheetLine && actions.setAi(sheetLine.id, 'explain')),
    onTool: (mode) => closeSheetThen(() => sheetLine && actions.setAi(sheetLine.id, mode)),
    onDismissFlag: () => closeSheetThen(() => sheetLine && actions.dismissFlag(sheetLine.id)),
    onSnapToSpeech: () => closeSheetThen(() => sheetLine && actions.snapToSpeech?.(sheetLine.id)),
    onAddNote: () =>
      closeSheetThen(() => {
        if (!sheetLine) return
        const id = sheetLine.id
        void ctl.openEdit(id, true).then((ok) => ok && actions.setNote({ term: '', type: 'translation', text: '' }))
      }),
    onSplit: edits.doSplit,
    onMerge: edits.doMerge,
    onAdd: edits.doAdd,
    onAddAndTranscribe: edits.doAddAndTranscribe,
    onDelete: edits.doDelete,
    onSetLanguage: (lang, scope) => void edits.doSetLanguage(lang, scope),
  }
}
