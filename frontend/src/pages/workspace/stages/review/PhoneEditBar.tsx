import { buttonClass } from '../../../../components/uiClasses'
import { lineNumber } from '../../../../lineNumber'
import { DISCARD_WARNING } from './LineRow'
import type { ReviewLine } from '../../../../types/review'

interface Props {
  active: ReviewLine
  editing: boolean
  discardWarning?: boolean
  hasMedia: boolean
  onCancel: () => void
  onSave: () => void
  onSaveAndNext: () => void
  onPlay: () => void
  onMove: (delta: 1 | -1) => void
  onEdit: () => void
  // Simple's check queue: Skip for now and Looks right, next take the place of Prev and Next.
  queue?: { onSkip: () => void; onLooksRight: () => void }
}

// The phone action bar for the active line.
export function PhoneEditBar({ active, editing, discardWarning, hasMedia, onCancel, onSave, onSaveAndNext, onPlay, onMove, onEdit, queue }: Props) {
  return (
    <div className={queue ? 'review-editbar is-queue' : 'review-editbar'} role="toolbar" aria-label="Line actions" onMouseDown={editing ? (e) => e.preventDefault() : undefined}>
      {editing ? (
        <>
          {discardWarning && <span role="status">{DISCARD_WARNING}</span>}
          <button type="button" className={buttonClass('ghost')} onClick={onCancel}>Cancel</button>
          <button type="button" className={buttonClass('secondary')} onClick={onSave}>Save</button>
          <button type="button" className={buttonClass('primary')} onClick={onSaveAndNext}>Save &amp; next</button>
        </>
      ) : (
        <>
          {hasMedia && (
            <button type="button" aria-label={`Play line ${lineNumber(active.idx)}`} onClick={onPlay}>
              ▶ #{lineNumber(active.idx)}
            </button>
          )}
          {queue ? (
            <button type="button" onClick={queue.onSkip}>Skip for now</button>
          ) : (
            <>
              <button type="button" aria-label="Previous line" onClick={() => onMove(-1)}>‹ Prev</button>
              <button type="button" aria-label="Next line" onClick={() => onMove(1)}>Next ›</button>
            </>
          )}
          <button type="button" onClick={onEdit}>
            Edit #{lineNumber(active.idx)}
          </button>
          {queue && !active.reviewed && (
            <button type="button" className={buttonClass('primary')} disabled={!active.en} onClick={queue.onLooksRight}>Looks right, next</button>
          )}
        </>
      )}
    </div>
  )
}
