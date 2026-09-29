import { useState } from 'react'

import { applyFindReplace, previewFindReplace } from '../../../../api/review'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { Field } from '../../../../components/Field'
import { Toggle } from '../../../../components/Toggle'
import { buttonClass } from '../../../../components/uiClasses'
import type { ApplyResult, ReviewMatch } from '../../../../types/review'
import { staleLabels } from './reviewLogic'
import { lineNumber } from '../../../../lineNumber'

interface Props {
  dramaId: number
  onChanged: () => void
  onClose: () => void
}

// Opened from the toolbar's "Replace…" (open state remembered under the
// review.findreplace key). Preview first; apply skips lines changed since.
export function FindReplacePanel({ dramaId, onChanged, onClose }: Props) {
  const [find, setFind] = useState('')
  const [replace, setReplace] = useState('')
  const [caseSensitive, setCaseSensitive] = useState(false)
  const [useRegex, setUseRegex] = useState(false)
  const [matches, setMatches] = useState<ReviewMatch[] | null>(null)
  const [result, setResult] = useState<{ r: ApplyResult; tried: ReviewMatch[] } | null>(null)
  const [error, setError] = useState<unknown>(null)

  const preview = () => {
    setResult(null)
    previewFindReplace(dramaId, { find, replace, case_sensitive: caseSensitive, use_regex: useRegex }).then(
      (m) => {
        setError(null)
        setMatches(m)
      },
      setError,
    )
  }

  const apply = () => {
    if (!matches?.length) return
    applyFindReplace(dramaId, matches).then((r) => {
      setError(null)
      setResult({ r, tried: matches })
      setMatches(null)
      onChanged()
    }, setError)
  }

  return (
    <section className="review-replace" aria-label="Find and replace">
      <div className="review-replace-head">
        <h3>Find and replace</h3>
        <button type="button" className={buttonClass('ghost', 'sm')} onClick={onClose}>Close</button>
      </div>
      <div className="filters">
        <input aria-label="Find" value={find} onChange={(e) => { setFind(e.target.value); setMatches(null) }} />
        <input aria-label="Replace with" value={replace} onChange={(e) => { setReplace(e.target.value); setMatches(null) }} />
      </div>
      <div className="setting-list review-toggles">
        <Field label="Match case">
          <Toggle checked={caseSensitive} onChange={(on) => { setCaseSensitive(on); setMatches(null) }} />
        </Field>
        <Field label="Regular expression">
          <Toggle checked={useRegex} onChange={(on) => { setUseRegex(on); setMatches(null) }} />
        </Field>
      </div>
      <div className="review-actions">
        <button type="button" className={buttonClass('secondary')} disabled={!find} onClick={preview}>Preview</button>
        <button type="button" className={buttonClass('secondary')} disabled={!matches?.length} onClick={apply}>
          Apply {matches?.length ?? 0} change{matches?.length === 1 ? '' : 's'}
        </button>
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {matches && (
        <ul className="review-matches" data-testid="fr-matches">
          {matches.length === 0 && <li className="muted">No matches.</li>}
          {matches.map((m) => (
            <li key={m.id}>
              <span className="muted">#{lineNumber(m.idx)}</span> <del>{m.old_text}</del> <ins>{m.new_text}</ins>
            </li>
          ))}
        </ul>
      )}
      {result && (
        <div role="status" data-testid="fr-result">
          Applied {result.r.applied}; skipped {result.r.stale} that changed since the preview.
          {result.r.stale > 0 && (
            <div data-testid="fr-stale">Skipped: {staleLabels(result.r.stale_ids, result.tried).join(', ')}</div>
          )}
        </div>
      )}
    </section>
  )
}
