import { useState } from 'react'

import { applyFindReplace, previewFindReplace } from '../../../../api/review'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import type { ApplyResult, ReviewMatch } from '../../../../types/review'
import { staleLabels } from './reviewLogic'

interface Props {
  dramaId: number
  onChanged: () => void
}

export function FindReplacePanel({ dramaId, onChanged }: Props) {
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
    <section className="panel" aria-label="Find and replace">
      <h3>Find and replace</h3>
      <div className="filters">
        <input aria-label="Find" value={find} onChange={(e) => { setFind(e.target.value); setMatches(null) }} />
        <input aria-label="Replace with" value={replace} onChange={(e) => { setReplace(e.target.value); setMatches(null) }} />
      </div>
      <label>
        <input type="checkbox" checked={caseSensitive} onChange={(e) => { setCaseSensitive(e.target.checked); setMatches(null) }} />{' '}
        Match case
      </label>{' '}
      <label>
        <input type="checkbox" checked={useRegex} onChange={(e) => { setUseRegex(e.target.checked); setMatches(null) }} />{' '}
        Regular expression
      </label>
      <div className="review-actions">
        <button type="button" disabled={!find} onClick={preview}>Preview</button>
        <button type="button" disabled={!matches?.length} onClick={apply}>
          Apply {matches?.length ?? 0} change{matches?.length === 1 ? '' : 's'}
        </button>
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {matches && (
        <ul className="review-matches" data-testid="fr-matches">
          {matches.length === 0 && <li className="muted">No matches.</li>}
          {matches.map((m) => (
            <li key={m.id}>
              <span className="muted">#{m.idx}</span> <del>{m.old_text}</del> <ins>{m.new_text}</ins>
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
