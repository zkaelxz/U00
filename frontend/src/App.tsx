import { useEffect, useRef, useState } from 'react'

import type { AuthUser } from './api/auth'
import { api } from './api/client'
import type { MetaResponse } from './api/types'
import { gateView, menuUser, signOut, useSession } from './hooks/useSession'
import { RouteErrorBoundary } from './components/ErrorBoundary'
import { NotificationBell } from './components/NotificationBell'
import ComicPage from './pages/Comic'
import DiagnosticsPage from './pages/Diagnostics'
import DiscoverPage from './pages/Discover'
import LibraryPage from './pages/Library'
import LivePage from './pages/Live'
import LoginPage from './pages/Login'
import ReaderPage from './pages/Reader'
import SettingsPage from './pages/Settings'
import SourcesPage from './pages/Sources'
import TranslatePage from './pages/Translate'
import WorkspaceShell from './pages/workspace/WorkspaceShell'
import './pages/login.css'
import { ReportProblemButton } from './report/ReportProblem'
import { routeHref, useRoute } from './router'
import type { Route } from './router'

function ApiStatus() {
  const [meta, setMeta] = useState<MetaResponse | null>(null)
  const [down, setDown] = useState(false)

  useEffect(() => {
    api.meta().then(setMeta, () => setDown(true))
  }, [])

  if (down) return <span className="badge bad">API unreachable</span>
  if (!meta) return <span className="badge">Connecting…</span>
  return (
    <span className="badge ok" data-testid="api-status">
      API v{meta.api_version} · {meta.environment}
    </span>
  )
}

// Signed in with auth on: the account and "Sign out". Absent with auth off.
function UserMenu({ user }: { user: AuthUser }) {
  const ref = useRef<HTMLDetailsElement>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const label = user.email ?? user.display_name ?? 'Signed in'

  // Close on a click elsewhere or Escape, like a menu.
  useEffect(() => {
    const close = (e: Event) => {
      const el = ref.current
      if (!el?.open) return
      if (e instanceof KeyboardEvent) {
        if (e.key !== 'Escape') return
        el.open = false
        el.querySelector('summary')?.focus()
      } else if (!el.contains(e.target as Node)) el.open = false
    }
    document.addEventListener('pointerdown', close)
    document.addEventListener('keydown', close)
    return () => {
      document.removeEventListener('pointerdown', close)
      document.removeEventListener('keydown', close)
    }
  }, [])

  async function onSignOut() {
    setBusy(true)
    setError(null)
    try {
      await signOut()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't sign out. Please try again.")
      setBusy(false)
    }
  }

  return (
    <details className="user-menu" ref={ref}>
      <summary aria-label={`Account: ${label}`}>
        <span className="user-menu-email">{label}</span>
      </summary>
      <div className="user-menu-panel">
        <p className="muted">Signed in as</p>
        <p className="user-menu-email" data-testid="user-email">{label}</p>
        <button type="button" onClick={onSignOut} disabled={busy}>
          {busy ? 'Signing out…' : 'Sign out'}
        </button>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
      </div>
    </details>
  )
}

// [label, target, route names that count as being on this page]
const NAV: [string, Route, Route['name'][]][] = [
  ['Library', { name: 'library' }, ['library', 'drama', 'read', 'comic']],
  ['Translate', { name: 'translate' }, ['translate']],
  ['Sources', { name: 'sources' }, ['sources']],
  ['Discover', { name: 'discover' }, ['discover']],
  ['Live', { name: 'live' }, ['live']],
  ['Settings', { name: 'settings' }, ['settings']],
  ['Diagnostics', { name: 'diagnostics' }, ['diagnostics']],
]

export default function App() {
  const route = useRoute()
  const session = useSession()
  const view = gateView(session)

  if (view === 'connecting') {
    return (
      <p className="muted connecting" role="status">
        Connecting…
      </p>
    )
  }
  if (view === 'login') {
    return <LoginPage configured={session.status !== 'ready' || session.me.sign_in_configured} />
  }
  const user = menuUser(session)

  return (
    <>
      <header className="app-header">
        <h1>Baihe Studio</h1>
        <nav aria-label="Main">
          {NAV.map(([label, target, active]) => (
            <a
              key={label}
              href={routeHref(target)}
              aria-current={active.includes(route.name) ? 'page' : undefined}
            >
              {label}
            </a>
          ))}
        </nav>
        <div className="header-end">
          <NotificationBell />
          <ReportProblemButton />
          <ApiStatus />
          {user && <UserMenu user={user} />}
        </div>
      </header>
      {/* Header and nav stay outside the boundary so a crashed page can still be left. */}
      <RouteErrorBoundary>
        {route.name === 'library' && <LibraryPage />}
        {route.name === 'drama' && <WorkspaceShell id={route.id} stage={route.stage} />}
        {route.name === 'read' && <ReaderPage key={route.id} id={route.id} page={route.page} />}
        {route.name === 'comic' && <ComicPage key={route.id} id={route.id} page={route.page} />}
        {route.name === 'settings' && <SettingsPage />}
        {route.name === 'translate' && <TranslatePage />}
        {route.name === 'sources' && <SourcesPage />}
        {route.name === 'discover' && <DiscoverPage />}
        {route.name === 'live' && <LivePage />}
        {route.name === 'diagnostics' && <DiagnosticsPage />}
      </RouteErrorBoundary>
    </>
  )
}
