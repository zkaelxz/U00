import { useState } from 'react'

import { Field } from '../../../../components/Field'
import { buttonClass } from '../../../../components/uiClasses'
import type { ReviewLine } from '../../../../types/review'
import { lineRange, MAX_MERGE_LINES, mergedText } from './reviewLogic'

interface Props {
  // The line to merge into, followed by the lines after it on this page.
  run: ReviewLine[]
  busy: boolean
  blocked: string | null
  onMerge: (lineIds: number[]) => void
  onCancel: () => void
}

// Merge the line with the next one (or several). The first line keeps its id,
// flag and notes; the preview shows the joined text before anything changes.
export function MergeConfirm({ run, busy, blocked, onMerge, onCancel }: Props) {
  const max = Math.min(run.length, MAX_MERGE_LINES)
  const [count, setCount] = useState(Math.min(2, max))
  if (max < 2) {
    return (
      <div className="review-form">
        <p className="muted">There is no next line on this page to merge with. Go to the next page and merge from there.</p>
        <div className="actions"><button type="button" onClick={onCancel}>Back</button></div>
      </div>
    )
  }
  const chosen = run.slice(0, count)
  const joined = mergedText(chosen)
  const label = `Merge ${lineRange(chosen)}`
  return (
    <form
      className="review-form"
      onSubmit={(e) => {
        e.preventDefault()
        if (!busy && !blocked) onMerge(chosen.map((l) => l.id))
      }}
    >
      {max > 2 && (
        <Field label="Lines to merge" help={`2 to ${max}: this line and the ones after it on this page.`}>
          <input
            type="number"
            inputMode="numeric"
            min={2}
            max={max}
            value={count}
            onChange={(e) => setCount(Math.min(max, Math.max(2, Number(e.target.value) || 2)))}
          />
        </Field>
      )}
      <div className="review-preview" data-testid="merge-preview">
        <div lang="zh">{joined.zh}</div>
        <div>{joined.en || <span className="muted">(no translation)</span>}</div>
      </div>
      {blocked && <p className="muted">{blocked}</p>}
      <div className="actions">
        <button type="submit" className={buttonClass('primary')} disabled={busy || !!blocked}>
          {busy ? 'Merging…' : label}
        </button>
        <button type="button" className={buttonClass('ghost')} onClick={onCancel}>Back</button>
      </div>
    </form>
  )
}
