import { routeHref } from '../router'
import { describeError, type DescribeOptions } from './errorMessages'

export function ErrorBanner({ error, onDismiss, describe }: {
  error: unknown
  onDismiss?: () => void
  // Opt-in copy: { pcOnly } for PC-only calls, { serverText } for admin/restore calls.
  describe?: DescribeOptions
}) {
  if (!error) return null
  const { title, detail } = describeError(error, describe)
  return (
    <div className="banner error-banner" role="alert">
      <div>
        <strong>{title}</strong>
        {detail && <div className="muted">{detail}</div>}
        {(error as { code?: string }).code === 'extension_only' && (
          <div><a href={routeHref({ name: 'settings' })}>Extension help</a></div>
        )}
      </div>
      {onDismiss && (
        <button type="button" className="link" onClick={onDismiss}>
          Dismiss
        </button>
      )}
    </div>
  )
}
