import { useEffect, useRef, useState } from 'react'

import { loginHref, type AuthUser } from './api/auth'
import { api } from './api/client'
import { useMediaQuery } from './hooks/useMediaQuery'
import { useDetailsMenu } from './hooks/useDetailsMenu'
import { JobsProvider } from './hooks/JobsProvider'
import { canSignOut, gateView, menuUser, recheckSession, sessionExpired, signOut, useSession } from './hooks/useSession'
import { RouteErrorBoundary } from './components/ErrorBoundary'
import AdminPage from './pages/Admin'
import AssistantPage from './pages/Assistant'
import { useDeveloperMode } from './pages/assistant/developerMode'
import { JobsMenu } from './components/JobsMenu'
import { NotificationBell } from './components/NotificationBell'
import { RemoteHealthBanner } from './components/RemoteHealthBanner'
import { ThemeMenu } from './components/ThemeMenu'
import ComicPage from './pages/Comic'
import BenchmarkPage from './pages/Benchmark'
import DiagnosticsPage from './pages/Diagnostics'
import DiscoverPage from './pages/Discover'
import JobsPage from './pages/Jobs'
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
import { CommandPalette } from './nav/CommandPalette'
import { useHiddenNav } from './nav/hiddenNav'
import { NavDrawer } from './nav/NavDrawer'
import { SideNav } from './nav/SideNav'
import { useRailCollapsed } from './nav/useRailCollapsed'
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
// With /me unavailable the user is unknown, but Sign out stays reachable.
function UserMenu({ user }: { user: AuthUser | null }) {
  const ref = useDetailsMenu()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const label = user ? (user.email ?? user.display_name ?? 'Signed in') : 'Account'

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
        {user ? (
          <>
            <p className="muted">Signed in as</p>
            <p className="user-menu-email" data-testid="user-email">{label}</p>
          </>
        ) : (
          <p className="muted">Can't check who is signed in right now.</p>
        )}
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

// A 401 after the app was on screen. Signing in here in a new tab keeps this
// tab's unsaved drafts; a same-tab redirect to Google would unload them.
function SignInOverlay({ configured }: { configured: boolean }) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    // A modal dialog, so it sits in the top layer above any open Sheet and makes the app beneath inert.
    const d = ref.current
    if (d && !d.open) {
      if (typeof d.showModal === 'function') d.showModal()
      else d.setAttribute('open', '')
    }
    const onFocus = () => void recheckSession()
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [])
  const returnTo = `${window.location.pathname}${window.location.hash}`

  return (
    <dialog ref={ref} className="signin-overlay" aria-labelledby="signin-overlay-title" onCancel={(e) => e.preventDefault()}>
      <section className="panel login-card">
        <h2 id="signin-overlay-title">You've been signed out</h2>
        {configured ? (
          <>
            <p className="muted">Your unsaved work is still here. Sign in in a new tab, then come back to this one.</p>
            <a className="login-button" href={loginHref(returnTo)} target="_blank" rel="noopener noreferrer">
              Sign in with Google (new tab)
            </a>
          </>
        ) : (
          <p className="muted">Sign-in isn't set up on the PC yet. Your unsaved work is still here.</p>
        )}
        <button type="button" onClick={() => void recheckSession()}>
          I've signed in
        </button>
      </section>
    </dialog>
  )
}

// List and card pages use the wider column; forms and reading pages keep the 1200px cap.
const WIDE_ROUTES: ReadonlySet<string> = new Set([
  'library',
  'library-tools',
  'jobs',
  'sources',
  'discover',
  'diagnostics',
  'manga',
  'manga-series',
])

export default function App() {
  const route = useRoute()
  const session = useSession()
  const view = gateView(session)
  const developerMode = useDeveloperMode(view === 'app')
  const pcMode = usePcOnly()
  const [hidden] = useHiddenNav(session)
  const wide = useMediaQuery('(min-width: 1024px)')
  const [railCollapsed, toggleRail] = useRailCollapsed()

  if (view === 'connecting') {
    return (
      <p className="muted connecting" role="status">
        Connecting…
      </p>
    )
  }
  const signInConfigured = session.status !== 'ready' || session.me.sign_in_configured
  const signedOut = sessionExpired(session)
  if (view === 'login' && !signedOut) return <LoginPage configured={signInConfigured} />
  const user = menuUser(session)
  const navContext = { session, pcMode, developerMode, hidden }

  const headerEnd = (
    <div className="header-end">
      <CommandPalette route={route} context={navContext} />
      <JobsMenu />
      <NotificationBell />
      <ReportProblemButton />
      <ThemeMenu />
      <ApiStatus />
      {canSignOut(session) && <UserMenu user={user} />}
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
        {route.name === 'jobs' && <JobsPage />}
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

  // One tree at every width, so crossing 1024px keeps the open page (and a playing video) mounted.
  return (
    <JobsProvider>
      {signedOut && <SignInOverlay configured={signInConfigured} />}
      <div className={wide ? 'app-shell has-rail' : 'app-shell'}>
        {wide && <SideNav route={route} context={navContext} collapsed={railCollapsed} onToggle={toggleRail} />}
        <div className="app-main" data-width={WIDE_ROUTES.has(route.name) ? 'wide' : undefined}>
          {wide ? (
            <header className="app-header">{headerEnd}</header>
          ) : (
            <header className="app-header">
              <NavDrawer route={route} context={navContext} />
              <h1>
                <a href={routeHref({ name: 'library' })}>
                  Baihe<span className="title-rest"> Studio</span>
                </a>
              </h1>
              {headerEnd}
            </header>
          )}
          {content}
        </div>
      </div>
    </JobsProvider>
  )
}
