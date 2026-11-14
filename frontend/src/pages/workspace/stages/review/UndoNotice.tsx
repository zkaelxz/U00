import { buttonClass } from '../../../../components/uiClasses'

interface Props {
  message: string
  busy: boolean
  onUndo: () => void
  onDismiss: () => void
  testId?: string
  // Structural edits also land in line history; a speaker change does not.
  historyNote?: boolean
  className?: string
}

// The confirmation of a structural edit with a one-click Undo. It stays until
// dismissed, the next edit or an edit saved to a line (the caller clears it);
// Records -> Line history stays the fallback and is named in the text.
export function UndoNotice({ message, busy, onUndo, onDismiss, testId = 'undo-notice', historyNote = true, className }: Props) {
  return (
    <div className={['review-undo', className].filter(Boolean).join(' ')} role="status" data-testid={testId}>
      <span className="review-undo-text">
        {message} {historyNote && <span className="muted">Records → Line history also has it.</span>}
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
