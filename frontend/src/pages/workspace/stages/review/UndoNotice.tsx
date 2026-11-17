import { useEffect, useRef } from 'react'

import { isTextEntry } from '../../../../hooks/useShortcut'
import { buttonClass } from '../../../../components/uiClasses'
import { isUndoKey, mentionsLineHistory } from './reviewLogic'

interface Props {
  message: string
  busy: boolean
  onUndo: () => void
  onDismiss: () => void
  // Opens Records → Line history, which Simple leaves out of the Review tools until asked for.
  onOpenHistory?: () => void
  testId?: string
  // Structural edits also land in line history; a speaker change does not.
  historyNote?: boolean
  className?: string
}

// "Line history" as a button wherever Review's words send someone there, so the pointer works in Simple too.
export function LineHistoryButton({ onOpen }: { onOpen: () => void }) {
  return (
    <button type="button" className={buttonClass('ghost', 'sm')} onClick={onOpen}>
      Line history
    </button>
  )
}

// The lines' status message; one that names Line history carries the button that opens it.
export function ReviewStatus({ text, onOpenHistory }: { text: string | null; onOpenHistory?: () => void }) {
  return (
    <p className="review-status" role="status">
      {text}
      {onOpenHistory && mentionsLineHistory(text) && <> <LineHistoryButton onOpen={onOpenHistory} /></>}
    </p>
  )
}

// The confirmation of a structural edit with a one-click Undo. It stays until
// dismissed, the next edit or an edit saved to a line (the caller clears it);
// Records -> Line history stays the fallback and is named in the text.
export function UndoNotice({ message, busy, onUndo, onDismiss, onOpenHistory, testId = 'undo-notice', historyNote = true, className }: Props) {
  const undoRef = useRef({ busy, onUndo })
  useEffect(() => {
    undoRef.current = { busy, onUndo }
  })
  // useShortcut drops every Ctrl/Meta combo, so this listens itself. Text fields keep
  // their own text undo, and an IME composition must not be interrupted.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || undoRef.current.busy || !isUndoKey(e, isTextEntry(e.target))) return
      e.preventDefault()
      undoRef.current.onUndo()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])
  return (
    <div className={['review-undo', className].filter(Boolean).join(' ')} role="status" data-testid={testId}>
      <span className="review-undo-text">
        {message} {historyNote && <span className="muted">Records → Line history also has it.</span>}
        {historyNote && onOpenHistory && <LineHistoryButton onOpen={onOpenHistory} />}
      </span>
      <span className="review-undo-actions">
        <button type="button" className={buttonClass('primary')} disabled={busy} onClick={onUndo}>
          Undo
        </button>
        <button type="button" className={buttonClass('secondary')} aria-label="Dismiss" onClick={onDismiss}>
          ×
        </button>
      </span>
    </div>
  )
}
