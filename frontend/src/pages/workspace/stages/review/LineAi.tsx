import { useEffect, useRef, useState } from 'react'

import { ApiError } from '../../../../api/client'
import { explainLine, improveLine } from '../../../../api/review'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import type { LineExplanation, LineImprovement, ReviewLine } from '../../../../types/review'
import { AI_STALE_MESSAGE, AI_UNAVAILABLE_MESSAGE, suggestionIsStale } from './reviewLogic'

export type AiMode = 'improve' | 'explain'

interface Props {
  dramaId: number
  line: ReviewLine
  mode: AiMode
  onClose: () => void
  // Applies text through the editor's compare-and-set patch; true when saved.
  onUse: (text: string) => Promise<boolean>
}

// The per-line AI panel, opened from the active row's toolbar or the line
// sheet. Requests only read; nothing is saved until "Use this", and only one
// request per line runs at a time. "Why this?" asks straight away.
export function LineAi({ dramaId, line, mode, onClose, onUse }: Props) {
  const [issue, setIssue] = useState('')
  const [busy, setBusy] = useState(false)
  const [improvement, setImprovement] = useState<LineImprovement | null>(null)
  const [explanation, setExplanation] = useState<LineExplanation | null>(null)
  const [unavailable, setUnavailable] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const busyRef = useRef(false)

  const fail = (e: unknown) => {
    if (e instanceof ApiError && e.status === 503) setUnavailable(true)
    else setError(e)
  }
  const run = (m: AiMode) => {
    if (busyRef.current) return
    busyRef.current = true
    setBusy(true)
    setImprovement(null)
    setExplanation(null)
    setUnavailable(false)
    setError(null)
    const done = () => {
      busyRef.current = false
      setBusy(false)
    }
    if (m === 'improve') improveLine(dramaId, line.id, issue).then(setImprovement, fail).finally(done)
    else explainLine(dramaId, line.id).then(setExplanation, fail).finally(done)
  }

  // Ask once on open for an explanation (the panel is keyed by line and mode).
  const asked = useRef(false)
  useEffect(() => {
    if (mode === 'explain' && !asked.current) {
      asked.current = true
      run('explain')
    }
  })

  const use = (text: string) => {
    setBusy(true)
    onUse(text).then((ok) => ok && onClose()).finally(() => setBusy(false))
  }
  const stale = improvement ? suggestionIsStale(line, improvement.current_en) : false

  return (
    <div className="review-ai-panel" data-testid="line-ai-panel">
      {mode === 'improve' && (
        <>
          <div className="review-ai-ask">
            <label>
              What to fix (optional)
              <input
                value={issue}
                maxLength={500}
                placeholder="e.g. too formal"
                disabled={busy}
                onChange={(e) => setIssue(e.target.value)}
              />
            </label>
            <button type="button" disabled={busy} onClick={() => run('improve')}>
              {busy ? 'Working…' : improvement ? 'Ask again' : 'Suggest'}
            </button>
            <button type="button" className="link" disabled={busy} onClick={onClose}>
              Close
            </button>
          </div>
          {improvement && (
            <div data-testid="line-ai-result">
              {improvement.changed ? (
                <div className="review-ai-compare">
                  <div>
                    <span className="muted">Current</span>
                    <div className="review-ai-text">{improvement.current_en}</div>
                  </div>
                  <div>
                    <span className="muted">Suggestion</span>
                    <div className="review-ai-text" data-testid="line-ai-suggestion">{improvement.suggestion}</div>
                  </div>
                </div>
              ) : (
                <p className="muted">The model kept this line as it is.</p>
              )}
              {stale && <p className="error" role="alert">{AI_STALE_MESSAGE}</p>}
              {improvement.changed && (
                <div className="review-actions">
                  <button type="button" disabled={busy || stale} onClick={() => use(improvement.suggestion)}>
                    Use this
                  </button>
                  <button type="button" disabled={busy} onClick={onClose}>Dismiss suggestion</button>
                </div>
              )}
            </div>
          )}
        </>
      )}
      {mode === 'explain' && (
        <>
          {busy && <p className="muted">Working…</p>}
          {explanation && (
            <p className="review-ai-text" data-testid="line-ai-explanation">{explanation.explanation}</p>
          )}
          <div className="review-actions">
            <button type="button" className="link" disabled={busy} onClick={onClose}>
              Hide explanation
            </button>
          </div>
        </>
      )}
      {unavailable && (
        <p className="error" role="alert" data-testid="line-ai-unavailable">
          {AI_UNAVAILABLE_MESSAGE} <a href="#/settings">Open Settings</a>
        </p>
      )}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </div>
  )
}
