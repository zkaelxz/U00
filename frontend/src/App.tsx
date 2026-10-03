import { useEffect, useState } from 'react'

import type { AuthUser } from './api/auth'
import { api } from './api/client'
import { useDetailsMenu } from './hooks/useDetailsMenu'
import { gateView, menuUser, signOut, useSession } from './hooks/useSession'
import { RouteErrorBoundary } from './components/ErrorBoundary'
import AdminPage from './pages/Admin'
import AssistantPage from './pages/Assistant'
import { useDeveloperMode } from './pages/assistant/developerMode'
import { GearMenu, type GearItem } from './components/GearMenu'
import { JobsMenu } from './components/JobsMenu'
import { NotificationBell } from './components/NotificationBell'
import { RemoteHealthBanner } from './components/RemoteHealthBanner'
import { ThemeMenu } from './components/ThemeMenu'
import ComicPage from './pages/Comic'
import BenchmarkPage from './pages/Benchmark'
import DiagnosticsPage from './pages/Diagnostics'
import DiscoverPage from './pages/Discover'
import LibraryPage from './pages/Library'
import LibraryToolsPage from './pages/LibraryTools'
import LivePage from './pages/Live'
import LoginPage from './pages/Login'
import ReaderPage from './pages/Reader'
import SavedMangaPage from './pages/SavedManga'
import SavedMangaReader from './pages/SavedMangaReader'
import SettingsPage from './pages/Settings'
import SourcesPage from './pages/Sources'
import TranslatePage from './pages/Translate'
import WorkspaceShell from './pages/workspace/WorkspaceShell'
import './pages/login.css'
import { canViewUsers } from './pages/diagnostics/adminUsers'
import { ReportProblemButton } from './report/ReportProblem'
import { routeHref, useRoute } from './router'
import type { Route } from './router'

// Shown only when the server can't be reached; the version lives in
// Diagnostics and in problem reports, where it is useful.
function ApiStatus() {
  const [down, setDown] = useState(false)

  useEffect(() => {
    api.meta().catch(() => setDown(true))
  }, [])

  if (!down) return null
  return (
    <span className="badge bad" data-testid="api-status" title="Check that Baihe Studio is still running on this PC.">
      Can't reach Baihe
    </span>
  )
}

// Signed in with auth on: the account and "Sign out". Absent with auth off.
function UserMenu({ user }: { user: AuthUser }) {
  const ref = useDetailsMenu()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const label = user.email ?? user.display_name ?? 'Signed in'

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
  ['Library', { name: 'library' }, ['library', 'library-tools', 'drama', 'read', 'comic', 'manga', 'manga-series', 'manga-read']],
  ['Quick translate', { name: 'translate' }, ['translate']],
  ['Sources', { name: 'sources' }, ['sources']],
  ['Discover', { name: 'discover' }, ['discover']],
  ['Live', { name: 'live' }, ['live']],
]

// Behind the cogwheel: rarely used pages. Admin is for admins, the Assistant
// for Developer Mode (Settings; PC only).
function gearItems(admin: boolean, developerMode: boolean): GearItem[] {
  return [
    { label: 'Settings', target: { name: 'settings' }, active: ['settings'] },
    ...(admin ? [{ label: 'Admin', target: { name: 'admin' } as Route, active: ['admin'] as Route['name'][] }] : []),
    { label: 'Diagnostics', target: { name: 'diagnostics' }, active: ['diagnostics', 'benchmark'] },
    ...(developerMode ? [{ label: 'Assistant', target: { name: 'assistant' } as Route, active: ['assistant'] as Route['name'][] }] : []),
  ]
}

export default function App() {
  const route = useRoute()
  const session = useSession()
  const view = gateView(session)
  const developerMode = useDeveloperMode(view === 'app')

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
          <GearMenu items={gearItems(canViewUsers(session), developerMode)} route={route} />
        </nav>
        <div className="header-end">
          <JobsMenu />
          <NotificationBell />
          <ReportProblemButton />
          <ThemeMenu />
          <ApiStatus />
          {user && <UserMenu user={user} />}
        </div>
      </header>
      <RemoteHealthBanner />
      {/* Header and nav stay outside the boundary so a crashed page can still be left. */}
      <RouteErrorBoundary>
        {route.name === 'library' && <LibraryPage />}
        {route.name === 'library-tools' && <LibraryToolsPage />}
        {route.name === 'drama' && <WorkspaceShell id={route.id} stage={route.stage} />}
        {route.name === 'read' && <ReaderPage key={route.id} id={route.id} page={route.page} />}
        {route.name === 'comic' && <ComicPage key={route.id} id={route.id} page={route.page} />}
        {route.name === 'manga' && <SavedMangaPage />}
        {route.name === 'manga-series' && (
          <SavedMangaPage key={`${route.source}/${route.series}`} source={route.source} series={route.series} />
        )}
        {route.name === 'manga-read' && (
          <SavedMangaReader
            key={`${route.source}/${route.series}/${route.chapter}`}
            source={route.source}
            series={route.series}
            chapter={route.chapter}
            page={route.page}
          />
        )}
        {route.name === 'settings' && <SettingsPage />}
        {route.name === 'admin' && <AdminPage />}
        {route.name === 'translate' && <TranslatePage />}
        {route.name === 'sources' && <SourcesPage />}
        {route.name === 'discover' && <DiscoverPage />}
        {route.name === 'live' && <LivePage />}
        {route.name === 'diagnostics' && <DiagnosticsPage />}
        {route.name === 'assistant' && <AssistantPage />}
        {route.name === 'benchmark' && <BenchmarkPage compare={route.compare} />}
      </RouteErrorBoundary>
    </>
  )
}
