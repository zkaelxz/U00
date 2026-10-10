import { buttonClass } from '../../../../components/uiClasses'
import { lineNumber } from '../../../../lineNumber'
import type { ReviewLine } from '../../../../types/review'

interface Props {
  active: ReviewLine
  editing: boolean
  hasMedia: boolean
  onCancel: () => void
  onSave: () => void
  onSaveAndNext: () => void
  onPlay: () => void
  onMove: (delta: 1 | -1) => void
  onEdit: () => void
}

// The phone action bar for the active line.
export function PhoneEditBar({ active, editing, hasMedia, onCancel, onSave, onSaveAndNext, onPlay, onMove, onEdit }: Props) {
  return (
    <div className="review-editbar" role="toolbar" aria-label="Line actions">
      {editing ? (
        <>
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
          <button type="button" aria-label="Previous line" onClick={() => onMove(-1)}>‹ Prev</button>
          <button type="button" aria-label="Next line" onClick={() => onMove(1)}>Next ›</button>
          <button type="button" onClick={onEdit}>
            Edit #{lineNumber(active.idx)}
          </button>
        </>
      )}
    </div>
  )
}
