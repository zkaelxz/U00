import { buttonClass } from '../../../../components/uiClasses'
import { SELECTION_ACTIONS, type SelectionActionContext } from './selectionActions'

// Sticky bar shown only while lines are ticked: the count and the registered actions.
export function SelectionBar({ ctx }: { ctx: SelectionActionContext }) {
  const count = ctx.selectedIds.length
  return (
    <div className="review-selbar" role="toolbar" aria-label="Selected lines" data-testid="selection-bar">
      <span className="review-selbar-count">{count} selected</span>
      <span className="review-selbar-actions">
        {SELECTION_ACTIONS.map((a) => (
          <button key={a.id} type="button" className={buttonClass('secondary', 'sm')} onClick={() => void a.run(ctx)}>
            {a.label}
          </button>
        ))}
        <button type="button" className={buttonClass('ghost', 'sm')} onClick={ctx.clear}>
          Clear
        </button>
      </span>
    </div>
  )
}
