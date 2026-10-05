import { useEffect, useState } from 'react'

import type { AuthUser } from './api/auth'
import { api } from './api/client'
import { useMediaQuery } from './hooks/useMediaQuery'
import { usePersistedState } from './hooks/usePersistedState'
import { useDetailsMenu } from './hooks/useDetailsMenu'
import { gateView, menuUser, signOut, useSession } from './hooks/useSession'
import { RouteErrorBoundary } from './components/ErrorBoundary'
import AdminPage from './pages/Admin'
import AssistantPage from './pages/Assistant'
import { useDeveloperMode } from './pages/assistant/developerMode'
import { GearMenu } from './components/GearMenu'
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
import { RAIL_COLLAPSED_KEY, visibleNavItems } from './nav/navItems'
import { SideNav } from './nav/SideNav'
import { ReportProblemButton } from './report/ReportProblem'
import { usePcOnly } from './hooks/usePcOnly'
import { routeHref, useRoute } from './router'

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

export default function App() {
  const route = useRoute()
  const session = useSession()
  const view = gateView(session)
  const developerMode = useDeveloperMode(view === 'app')
  const pcMode = usePcOnly()
  const wide = useMediaQuery('(min-width: 1024px)')
  const [railCollapsed, setRailCollapsed] = usePersistedState(RAIL_COLLAPSED_KEY, false)

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
  const navContext = { session, pcMode, developerMode }

  const headerEnd = (withGear: boolean) => (
    <div className="header-end">
      <JobsMenu />
      <NotificationBell />
      <ReportProblemButton />
      <ThemeMenu />
      {withGear && <GearMenu items={visibleNavItems('gear', navContext)} route={route} />}
      <ApiStatus />
      {user && <UserMenu user={user} />}
    </div>
  )

  // Header and nav stay outside the boundary so a crashed page can still be left.
  const content = (
    <>
      <RemoteHealthBanner />
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

  if (wide) {
    return (
      <div className="app-shell">
        <SideNav route={route} context={navContext} collapsed={railCollapsed} onToggle={() => setRailCollapsed(!railCollapsed)} />
        <div className="app-main">
          <header className="app-header">{headerEnd(false)}</header>
          {content}
        </div>
      </div>
    )
  }

  return (
    <>
      <header className="app-header">
        <h1>
          Baihe<span className="title-rest"> Studio</span>
        </h1>
        <nav aria-label="Main">
          {visibleNavItems('header', navContext).map(({ label, target, active }) => (
            <a
              key={label}
              href={routeHref(target)}
              aria-current={active.includes(route.name) ? 'page' : undefined}
            >
              {label}
            </a>
          ))}
        </nav>
        {headerEnd(true)}
      </header>
      {content}
    </>
  )
}
