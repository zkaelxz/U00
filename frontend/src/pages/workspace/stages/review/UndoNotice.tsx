import { buttonClass } from '../../../../components/uiClasses'

interface Props {
  message: string
  busy: boolean
  onUndo: () => void
  onDismiss: () => void
  // Opens Records → Line history, which Simple leaves out of the Review tools until asked for.
  onOpenHistory?: () => void
  testId?: string
}

// "Line history" as a button wherever Review's words send someone there, so the pointer works in Simple too.
export function LineHistoryButton({ onOpen }: { onOpen: () => void }) {
  return (
    <button type="button" className={buttonClass('ghost', 'sm')} onClick={onOpen}>
      Line history
    </button>
  )
}

// The confirmation of a structural edit with a one-click Undo. It stays until
// dismissed, the next edit or an edit saved to a line (the caller clears it);
// Records -> Line history stays the fallback and is named in the text.
export function UndoNotice({ message, busy, onUndo, onDismiss, onOpenHistory, testId = 'undo-notice' }: Props) {
  return (
    <div className="review-undo" role="status" data-testid={testId}>
      <span className="review-undo-text">
        {message} <span className="muted">Records → Line history also has it.</span>
        {onOpenHistory && <LineHistoryButton onOpen={onOpenHistory} />}
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
