import { routeHref } from '../router'
import { SettingsMentions } from './SettingLink'
import { errorAction } from './errorActions'
import { describeError, type DescribeOptions } from './errorMessages'

export function ErrorBanner({ error, onDismiss, describe }: {
  error: unknown
  onDismiss?: () => void
  // Opt-in copy: { pcOnly } for PC-only calls, { serverText } for admin/restore calls.
  describe?: DescribeOptions
}) {
  if (!error) return null
  const { title, detail } = describeError(error, describe)
  const action = errorAction(error)
  const extensionHelp = (error as { code?: string }).code === 'extension_only'
  // A banner that already ends in a link to Settings keeps its text plain, so it doesn't offer the same link twice.
  const text = (t: string) => (extensionHelp || action?.href === routeHref({ name: 'settings' }) ? t : <SettingsMentions text={t} />)
  return (
    <div className="banner error-banner" role="alert">
      <div>
        <strong>{text(title)}</strong>
        {detail && <div className="muted">{text(detail)}</div>}
        {action && <div><a href={action.href}>{action.label}</a></div>}
        {extensionHelp && (
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
