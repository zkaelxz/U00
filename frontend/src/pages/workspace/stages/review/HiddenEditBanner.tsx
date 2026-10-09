import { buttonClass } from '../../../../components/uiClasses'
import { lineNumber } from '../../../../lineNumber'
import type { EditState, RowIssue } from './LineRow'

interface Props {
  edit: EditState
  issue: RowIssue | null
  onSave: () => void
  onShow: () => void
  onDiscard: () => void
}

// A draft whose row is outside the current view: it can always be saved,
// brought back into view or discarded from here.
export function HiddenEditBanner({ edit, issue, onSave, onShow, onDiscard }: Props) {
  return (
    <div className="banner review-hidden-edit" role="alert" data-testid="hidden-edit">
      <span>
        Your edit to #{lineNumber(edit.base.idx)} is outside this view.
        {issue?.lineId === edit.lineId && issue.conflict && ' It changed elsewhere, so it could not be saved.'}
      </span>
      <span className="actions">
        <button type="button" className={buttonClass('primary', 'sm')} onClick={onSave}>Save</button>
        <button type="button" className={buttonClass('secondary', 'sm')} onClick={onShow}>Show</button>
        <button type="button" className={buttonClass('ghost', 'sm')} onClick={onDiscard}>Discard</button>
      </span>
    </div>
  )
}
