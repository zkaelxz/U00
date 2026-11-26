import { useMediaQuery } from '../hooks/useMediaQuery'
import { routeHref } from '../router'
import { SettingsMentions } from './SettingLink'
import { errorAction } from './errorActions'
import { describeError, type DescribeOptions } from './errorMessages'

const EXTENSION_ONLY_PHONE = 'This site only works through Baihe Grab. Open the chapter in the Grab app on your phone.'

export function ErrorBanner({ error, onDismiss, describe }: {
  error: unknown
  onDismiss?: () => void
  // Opt-in copy: { pcOnly } for PC-only calls, { serverText } for admin/restore calls.
  describe?: DescribeOptions
}) {
  // The phone has no browser extension; its way in is the Grab app.
  const phone = useMediaQuery('(max-width: 640px)')
  if (!error) return null
  const described = describeError(error, describe)
  const extensionHelp = (error as { code?: string }).code === 'extension_only'
  const title = extensionHelp && phone ? EXTENSION_ONLY_PHONE : described.title
  const { detail } = described
  const action = errorAction(error)
  // A banner that already ends in a link to Settings keeps its text plain, so it doesn't offer the same link twice.
  const text = (t: string) => (extensionHelp || action?.href === routeHref({ name: 'settings' }) ? t : <SettingsMentions text={t} />)
  return (
    <div className="banner error-banner" role="alert">
      <div>
        <strong>{text(title)}</strong>
        {detail && <div className="muted">{text(detail)}</div>}
        {action && <div><a href={action.href}>{action.label}</a></div>}
        {extensionHelp && !phone && (
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
