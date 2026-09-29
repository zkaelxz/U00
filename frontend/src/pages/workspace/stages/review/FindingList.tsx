import { useState } from 'react'

import { FINDINGS_SHOWN, type Finding, type GoToLine } from './reviewResults'

// A list of findings, each opening its line in the editor (by line id). Long
// lists show the first few and a "Show all" button.
export function FindingList({ items, onGoTo, testId }: { items: Finding[]; onGoTo: GoToLine; testId?: string }) {
  const [all, setAll] = useState(false)
  const shown = all ? items : items.slice(0, FINDINGS_SHOWN)
  return (
    <>
      <ul className="review-findings" data-testid={testId}>
        {shown.map((f) => (
          <li key={f.key}>
            {f.lineId !== null ? (
              <button
                type="button"
                className="link review-jump"
                title="Open this line in the editor"
                onClick={() => onGoTo({ lineId: f.lineId as number })}
              >
                {f.where}
              </button>
            ) : (
              <span className="muted review-jump">{f.where}</span>
            )}{' '}
            <span className="review-finding-text">{f.text}</span>
          </li>
        ))}
      </ul>
      {items.length > shown.length && (
        <button type="button" className="link review-jump" onClick={() => setAll(true)}>
          Show all {items.length}
        </button>
      )}
    </>
  )
}
