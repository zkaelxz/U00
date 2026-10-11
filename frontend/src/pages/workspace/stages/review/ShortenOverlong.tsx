import { useState } from 'react'

import { ApiError } from '../../../../api/client'
import { shortenOverlong } from '../../../../api/review'
import { ConfirmButton } from '../../../../components/ConfirmButton'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { lineNumber } from '../../../../lineNumber'
import type { ShortenResult } from '../../../../types/review'
import { AI_UNAVAILABLE_MESSAGE } from './reviewLogic'
import { JOB_RUNNING_MESSAGE } from './reviewResegment'

export const TOO_LONG = 'too_long_for_slot'

// Review parity R28: an AI engine rewrites the lines the pacing check calls
// too long for their time slot. It overwrites their English (nothing else),
// so it asks first; the lines before it are saved in Line history, and a
// line edited while the engine ran is left alone. The result is kept by the
// parent, so it stays on screen after the fixed lines leave the pacing list.
export function ShortenOverlong({ dramaId, count, jobRunning, onChanged, result, setResult }: {
  dramaId: number
  count: number
  jobRunning: boolean
  onChanged: () => void
  result: ShortenResult | null
  setResult: (r: ShortenResult | null) => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [unavailable, setUnavailable] = useState(false)

  const run = () => {
    setBusy(true)
    setError(null)
    setUnavailable(false)
    setResult(null)
    shortenOverlong(dramaId)
      .then(
        (r) => {
          setResult(r)
          if (r.shortened > 0) onChanged()
        },
        (e: unknown) => {
          if (e instanceof ApiError && e.status === 503) setUnavailable(true)
          else setError(e)
        },
      )
      .finally(() => setBusy(false))
  }

  const name = `${count} overlong line${count === 1 ? '' : 's'}`
  return (
    <div className="review-shorten" data-testid="shorten-overlong">
      {count > 0 && (
      <ConfirmButton
        name={name}
        label="Shorten overlong lines (AI)…"
        verb="shorten"
        tone="primary"
        confirmLabel={`Confirm: rewrite the English of ${name}`}
        busy={busy}
        disabled={jobRunning}
        onConfirm={run}
      />
      )}
      {count > 0 && jobRunning && <span className="muted review-reason">{JOB_RUNNING_MESSAGE}</span>}
      {result && (
        <div role="status" data-testid="shorten-status">
          <p>
            {result.shortened === 0
              ? 'No line was changed.'
              : `Shortened ${result.shortened} line${result.shortened === 1 ? '' : 's'}. The lines before are saved in Records → Line history (it keeps the last 10 saves; runs in a row share one).`}
            {result.stale > 0 && ` ${result.stale} changed while the AI worked and were left as they are.`}
            {result.remaining > 0 && ` ${result.remaining} more are left; run it again for those.`}
          </p>
          {result.lines.length > 0 && (
            <ul className="review-diffs">
              {result.lines.map((l) => (
                <li key={l.id}>
                  <span className="muted">#{lineNumber(l.idx)}</span>
                  <div className="review-diff-pair">
                    <del>
                      <span className="sr-only">Before: </span>
                      {l.before}
                    </del>
                    <ins>
                      <span className="sr-only">After: </span>
                      {l.after}
                    </ins>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {unavailable && (
        <p className="error" role="alert">
          {AI_UNAVAILABLE_MESSAGE} <a href="#/settings">Open Settings</a>
        </p>
      )}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </div>
  )
}
