import { describeError, type DescribeOptions } from './errorMessages'
import './RetryBanner.css'

// For a failed load: says it could not load, shows the reason when the server
// gave a fixed one, and offers Retry. Text only; error text is never HTML.
export function RetryBanner({ error, onRetry, busy = false, what, describe, text }: {
  error: unknown
  // A caller's own sentence for the failure (its page maps errors itself); replaces the generic title.
  text?: string
  onRetry: () => void
  busy?: boolean
  // What failed to load, for loads whose error text cannot say it ("the page").
  what?: string
  describe?: DescribeOptions
}) {
  if (!error) return null
  const described = describeError(error, describe)
  const title = text ?? described.title
  const detail = text ? null : described.detail
  return (
    <div className="banner error-banner retry-banner" role="alert">
      <div>
        <strong>{title}</strong>
        {detail && <div className="muted">{detail}</div>}
        {what && !title.startsWith('Could not load') && <div className="muted">Could not load {what}.</div>}
      </div>
      <div className="retry-banner-actions">
        <button type="button" onClick={onRetry} disabled={busy}>
          Retry
        </button>
      </div>
    </div>
  )
}
