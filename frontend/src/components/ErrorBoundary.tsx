import { Component, useState } from 'react'
import type { ErrorInfo, ReactNode } from 'react'

import { useHolds } from '../hooks/useHolds'
import { routeHref, useRoute } from '../router'
import { copyText } from './clipboard'
import { errorText } from './errorFallbackText'

function ErrorFallback({ error }: { error: unknown }) {
  const [copied, setCopied] = useState<'idle' | 'ok' | 'failed'>('idle')
  const text = errorText(error)
  // The Diagnostics page is admin-only, so a member's crash screen must not send them there.
  const canDiagnose = useHolds('admin.diagnostics')
  return (
    <main className="error-fallback" role="alert" data-testid="error-fallback">
      <h2>This page hit an error.</h2>
      <p className="muted">
        The rest of the app still works: use the menu above{canDiagnose ? ', reload, or check Diagnostics' : ' or reload'}.
      </p>
      <pre className="error-fallback-text" data-testid="error-fallback-text">{text}</pre>
      <div className="error-fallback-actions">
        <button
          type="button"
          onClick={() => copyText(text).then((ok) => setCopied(ok ? 'ok' : 'failed'))}
        >
          {copied === 'ok' ? 'Copied' : copied === 'failed' ? 'Copy failed, select the text above' : 'Copy error'}
        </button>
        <button type="button" className="primary" onClick={() => window.location.reload()}>
          Reload
        </button>
        {canDiagnose && <a href="#/diagnostics">Open Diagnostics</a>}
      </div>
    </main>
  )
}

type Props = { resetKey: string; children: ReactNode }
type State = { error: unknown; hasError: boolean; key: string }

// Catches a render error in the page below it so one broken page shows a
// message instead of unmounting the whole app. A new resetKey (the route)
// clears it, so navigating away recovers.
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, hasError: false, key: this.props.resetKey }

  static getDerivedStateFromError(error: unknown): Partial<State> {
    return { error, hasError: true }
  }

  static getDerivedStateFromProps(props: Props, state: State): Partial<State> | null {
    if (props.resetKey === state.key) return null
    return { error: null, hasError: false, key: props.resetKey }
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    console.error('Page render error', error, info.componentStack)
  }

  render() {
    if (this.state.hasError) return <ErrorFallback error={this.state.error} />
    return this.props.children
  }
}

// The boundary used by the app: it resets whenever the route (hash) changes.
export function RouteErrorBoundary({ children }: { children: ReactNode }) {
  const route = useRoute()
  return <ErrorBoundary resetKey={routeHref(route)}>{children}</ErrorBoundary>
}
