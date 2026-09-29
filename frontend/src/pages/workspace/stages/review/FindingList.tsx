import { useState, type ReactNode } from 'react'

import { FINDINGS_SHOWN, type Finding, type GoToLine, type LineTarget } from './reviewResults'

// One finding: a line link and its text. When the line cannot be opened (it is
// gone, or an unsaved edit is in the way) the reason shows right here.
export function FindingRow({
  where,
  target,
  title = 'Open this line in the editor',
  onGoTo,
  children,
}: {
  where: string
  target: LineTarget | null
  title?: string
  onGoTo: GoToLine
  children: ReactNode
}) {
  const [message, setMessage] = useState<string | null>(null)
  const open = (t: LineTarget) => {
    setMessage(null)
    void onGoTo(t).then(setMessage)
  }
  return (
    <li>
      {target ? (
        <button type="button" className="link review-jump" title={title} onClick={() => open(target)}>
          {where}
        </button>
      ) : (
        <span className="muted review-jump">{where}</span>
      )}
      <span className="review-finding-text">
        {children}
        {message && (
          <span role="status" className="review-jump-msg">
            {message}
          </span>
        )}
      </span>
    </li>
  )
}

// A list of findings, each opening its line in the editor (by line id). Long
// lists show the first few and a "Show all" button.
export function FindingList({ items, onGoTo, testId }: { items: Finding[]; onGoTo: GoToLine; testId?: string }) {
  const [all, setAll] = useState(false)
  const shown = all ? items : items.slice(0, FINDINGS_SHOWN)
  return (
    <>
      <ul className="review-findings" data-testid={testId}>
        {shown.map((f) => (
          <FindingRow key={f.key} where={f.where} target={f.lineId !== null ? { lineId: f.lineId } : null} onGoTo={onGoTo}>
            {f.text}
          </FindingRow>
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
