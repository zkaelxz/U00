import { snapToSpeech, startTimingCheck } from '../../../../api/timingCheck'
import { acceptTm as acceptTmSuggestion, addNote, dismissFlag, patchLine } from '../../../../api/review'
import type { NoteDraft, RowActions } from './LineRow'
import type { LinesControllerDeps, LinesCore } from './linesController'
import { isDirty, type LineDraft } from './reviewDraft'
import { charCount, codePointOffset, suggestionPatch } from './reviewLogic'
import { dismissTmEverywhere } from './tmDismiss'
import { retireUndoOffer } from './undoOffer'

// The callbacks every line row calls. Stable for the life of the controller; they read the
// current panel state through `st` at call time.
export function buildRowActions(deps: LinesControllerDeps, core: LinesCore): RowActions {
  const { dramaId, st, player, selectRef, showOnPageRef, setIssue, setStatus, setActiveId, setError, setAi, setRetranscribeFocusId } = deps
  const { find, replaceLine, failLine, setEditNow, saveEdit, leaveEdit, beforeWrite, afterWrite, focusTo, activate, openEdit, openSheet, saveAndNext } = core
  return {
    activate: (id) => void activate(id),
    retranscribeFocused: () => setRetranscribeFocusId(null),
    openEdit: (id, details) => void openEdit(id, details),
    setDraft: (patch: Partial<LineDraft>) => {
      const cur = st.current.edit
      if (cur) setEditNow({ ...cur, draft: { ...cur.draft, ...patch }, discardWarning: false })
    },
    cancelEdit: () => {
      const cur = st.current.edit
      // Esc used to drop typed text silently; the first press only warns.
      if (cur && !cur.discardWarning && isDirty(cur.base, cur.draft)) {
        setEditNow({ ...cur, discardWarning: true })
        return
      }
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
      const blocked = await beforeWrite([id])
      if (blocked) {
        setStatus(blocked)
        return false
      }
      const line = find(id)
      if (!line) return false
      const patch = suggestionPatch(line, text)
      if (!patch) return true
      try {
        const saved = await patchLine(dramaId, id, patch)
        replaceLine(saved)
        afterWrite([id])
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
    checkTiming: () => {
      startTimingCheck(dramaId).then(
        () => {
          setStatus('Timing check started. Snap to speech appears on the lines it finds.')
          st.current.onChanged()
        },
        (e) => setError(e),
      )
    },
    snapToSpeech: async (id) => {
      if (!(await leaveEdit())) return
      snapToSpeech(dramaId, [id]).then((r) => {
        // Edited since the check: the server left it alone, so say the line changed.
        setIssue(r.stale_ids.includes(id) ? { lineId: id, conflict: true } : null)
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
    acceptTm: async (id, entryId, expectedEn) => {
      const blocked = await beforeWrite([id])
      if (blocked) {
        setStatus(blocked)
        return
      }
      acceptTmSuggestion(dramaId, id, entryId, expectedEn).then((saved) => {
        afterWrite([id])
        replaceLine(saved)
        setIssue(null)
        st.current.onChanged()
      }, (e) => failLine(id, e))
    },
    dismissTm: (s) => dismissTmEverywhere(dramaId, s),
    clearIssue: () => setIssue(null),
    showOnPage: (id) => void showOnPageRef.current(id),
    reload: () => {
      // Keep the typing: the reload rebases it instead of throwing it away.
      const cur = st.current.edit
      setEditNow(cur && isDirty(cur.base, cur.draft) ? { ...cur, rebaseOnLoad: true } : null)
      setIssue(null)
      st.current.onChanged()
    },
  }
}
