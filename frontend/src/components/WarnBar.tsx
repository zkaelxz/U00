// WarnBar: one thin yellow (or red) line with a message, at most one link or button, and an × to close.
import type { ReactNode } from 'react'

export function WarnBar({ children, action, onDismiss, tone = 'warn', role = 'status', shell, testId, title }: {
  children: ReactNode
  action?: ReactNode
  onDismiss?: () => void
  tone?: 'warn' | 'bad'
  role?: 'status' | 'alert'
  /** Sits in the app shell above the page: lines up with the page's edges and keeps a gap from its heading. */
  shell?: boolean
  testId?: string
  /** The whole message, for the tooltip when a wide screen cuts the line short. */
  title?: string
}) {
  const cls = ['warn-bar', tone === 'bad' && 'warn-bar-bad', shell && 'warn-bar-shell'].filter(Boolean).join(' ')
  return (
    <div className={cls} role={role} data-testid={testId} title={title}>
      <span className="warn-bar-text">{children}</span>
      {action && <span className="warn-bar-action">{action}</span>}
      {onDismiss && (
        <button type="button" className="warn-bar-close" aria-label="Dismiss" onClick={onDismiss}>
          <span aria-hidden="true">×</span>
        </button>
      )}
    </div>
  )
}
