import { useState } from 'react'

import { Sheet } from '../../../../components/Sheet'
import { buttonClass } from '../../../../components/uiClasses'
import { useMediaQuery } from '../../../../hooks/useMediaQuery'
import { SELECTION_ACTIONS, type SelectionActionContext } from './selectionActions'

// Sticky bar shown only while lines are ticked: the count and the registered actions.
export function SelectionBar({ ctx }: { ctx: SelectionActionContext }) {
  const count = ctx.selectedIds.length
  // On a phone the five actions wrap to one per row and the bar covers about a third of the screen, so it keeps
  // one row and lists the actions in a sheet, as the line's own actions are.
  const phone = useMediaQuery('(max-width: 640px)')
  const [sheetOpen, setSheetOpen] = useState(false)
  const actions = SELECTION_ACTIONS.map((a) => ({ ...a, why: a.unavailable?.(ctx) ?? null }))
  const clear = (
    <button type="button" className={buttonClass('ghost', 'sm')} onClick={ctx.clear}>
      Clear
    </button>
  )

  if (phone) {
    return (
      <div className="review-selbar" role="toolbar" aria-label="Selected lines" data-testid="selection-bar">
        <span className="review-selbar-count">{count} selected</span>
        <span className="review-selbar-actions">
          <button type="button" className={buttonClass('secondary', 'sm')} aria-haspopup="dialog" onClick={() => setSheetOpen(true)}>
            Actions…
          </button>
          {clear}
        </span>
        <Sheet open={sheetOpen} title={`${count} selected`} onClose={() => setSheetOpen(false)}>
          <ul className="sheet-menu" aria-label="Actions for the selected lines">
            {actions.map((a) => (
              <li key={a.id}>
                <button
                  type="button"
                  disabled={a.why !== null}
                  onClick={() => {
                    setSheetOpen(false)
                    void a.run(ctx)
                  }}
                >
                  {a.label}
                  {a.why && <span className="sheet-reason">{a.why}</span>}
                </button>
              </li>
            ))}
          </ul>
        </Sheet>
      </div>
    )
  }

  return (
    <div className="review-selbar" role="toolbar" aria-label="Selected lines" data-testid="selection-bar">
      <span className="review-selbar-count">{count} selected</span>
      <span className="review-selbar-actions">
        {actions.map((a) => (
          <button
            key={a.id}
            type="button"
            className={buttonClass('secondary', 'sm')}
            disabled={a.why !== null}
            aria-describedby={a.why ? `selbar-why-${a.id}` : undefined}
            onClick={() => void a.run(ctx)}
          >
            {a.label}
          </button>
        ))}
        {clear}
      </span>
      {actions.map((a) =>
        a.why ? (
          <p key={a.id} id={`selbar-why-${a.id}`} className="review-selbar-note muted" role="status">
            {a.label}: {a.why}
          </p>
        ) : null,
      )}
    </div>
  )
}
