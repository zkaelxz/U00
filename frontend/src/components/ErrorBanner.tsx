import { describeError } from './errorMessages'

export function ErrorBanner({ error, onDismiss }: { error: unknown; onDismiss?: () => void }) {
  if (!error) return null
  const { title, detail } = describeError(error)
  return (
    <div className="banner error-banner" role="alert">
      <div>
        <strong>{title}</strong>
        {detail && <div className="muted">{detail}</div>}
      </div>
      {onDismiss && (
        <button type="button" className="link" onClick={onDismiss}>
          Dismiss
        </button>
      )}
    </div>
  )
}
