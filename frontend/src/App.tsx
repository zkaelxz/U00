import { lazy, Suspense, useEffect, useRef, useState } from 'react'

import { loginHref, type AuthUser } from './api/auth'
import { api } from './api/client'
import { featureOn } from './api/features'
import { useFeatures } from './hooks/useFeatures'
import { useMediaQuery } from './hooks/useMediaQuery'
import { useDetailsMenu } from './hooks/useDetailsMenu'
import { JobsProvider } from './hooks/JobsProvider'
import { canSignOut, gateView, menuUser, recheckSession, sessionExpired, signOut, useSession } from './hooks/useSession'
import { RouteErrorBoundary } from './components/ErrorBoundary'
import { useDeveloperMode } from './pages/assistant/developerMode'
import { HelpTip } from './components/HelpTip'
import { JobsMenu } from './components/JobsMenu'
import { NotificationBell } from './components/NotificationBell'
import { RemoteHealthBanner } from './components/RemoteHealthBanner'
import { ThemeMenu } from './components/ThemeMenu'
import LibraryPage from './pages/Library'
import LoginPage from './pages/Login'
import './pages/login.css'
import { CommandPalette } from './nav/CommandPalette'
import { GlobalShortcuts } from './nav/GlobalShortcuts'
import { useDocumentTitle } from './nav/documentTitle'
import { useHiddenNav } from './nav/hiddenNav'
import { BackButton } from './nav/BackButton'
import { MenuButton, NavDrawer } from './nav/NavDrawer'
import { PhoneBar } from './nav/PhoneBar'
import { PopOutMenu } from './nav/PopOutMenu'
import { isPopOutWindow } from './nav/popOut'
import { DOCK_MIN_WIDTH } from './nav/navItems'
import { useNavDock, useNavPosition } from './nav/navDock'
import { SideNav } from './nav/SideNav'
import { useHeaderHeight } from './nav/useHeaderHeight'
import { ReportProblemButton } from './report/ReportProblem'
import { usePcOnly } from './hooks/usePcOnly'
import { routeHref, useRoute } from './router'

// Each page is its own chunk, so the first load downloads only the shell and the Library.
const AdminPage = lazy(() => import('./pages/Admin'))
const AssistantPage = lazy(() => import('./pages/Assistant'))
const ComicPage = lazy(() => import('./pages/Comic'))
const BenchmarkPage = lazy(() => import('./pages/Benchmark'))
const DiagnosticsPage = lazy(() => import('./pages/Diagnostics'))
const DiscoverPage = lazy(() => import('./pages/Discover'))
const JobsPage = lazy(() => import('./pages/Jobs'))
const LibraryToolsPage = lazy(() => import('./pages/LibraryTools'))
const LivePage = lazy(() => import('./pages/Live'))
const ReaderPage = lazy(() => import('./pages/Reader'))
const SavedMangaPage = lazy(() => import('./pages/SavedManga'))
const SavedMangaReader = lazy(() => import('./pages/SavedMangaReader'))
const SeriesPage = lazy(() => import('./pages/series/SeriesPage'))
const SettingsPage = lazy(() => import('./pages/Settings'))
const SourcesPage = lazy(() => import('./pages/Sources'))
const MakeSubtitlesPage = lazy(() => import('./pages/MakeSubtitlesPage'))
const TranslatePage = lazy(() => import('./pages/Translate'))
const WorkspaceShell = lazy(() => import('./pages/workspace/WorkspaceShell'))

// Shown only when the server can't be reached; the version lives in
// Diagnostics and in problem reports, where it is useful.
function ApiStatus() {
  const [down, setDown] = useState(false)

  useEffect(() => {
    api.meta().catch(() => setDown(true))
  }, [])

  if (!down) return null
  return (
    <span className="badge bad" data-testid="api-status">
      Can't reach Baihe
      <HelpTip label="Can't reach Baihe" id="api-status-help">Check that Baihe Studio is still running on this PC.</HelpTip>
    </span>
  )
}

// The display name wins over the email: an address's first letter is rarely the person's.
function accountInitial(user: AuthUser | null): string | null {
  const first = (user?.display_name || user?.email || '').trim().charAt(0)
  return /\p{L}|\p{N}/u.test(first) ? first.toLocaleUpperCase() : null
}

// Signed in with auth on: the account and "Sign out". Absent with auth off.
// With /me unavailable the user is unknown, but Sign out stays reachable.
function UserMenu({ user }: { user: AuthUser | null }) {
  const ref = useDetailsMenu()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const label = user ? (user.email ?? user.display_name ?? 'Signed in') : 'Account'
  const initial = accountInitial(user)

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
      <summary aria-label={`Account: ${user?.display_name ?? label}`}>
        <span className="user-menu-avatar" aria-hidden="true">
          {initial ?? (
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <circle cx="12" cy="8" r="4" />
              <path d="M4 21c0-4 3.6-7 8-7s8 3 8 7" />
            </svg>
          )}
        </span>
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

// The hash is the router's, so a plain #main-content link would navigate away; focus the landmark instead.
function skipToContent(e: React.MouseEvent) {
  e.preventDefault()
  document.getElementById('main-content')?.focus()
}

// Routes that render inside PageShell; the rest (a title's workspace, the readers) manage their own layout and
// are held to the 1200px column by .app-bounded.
const BOUNDED_ROUTES: ReadonlySet<string> = new Set(['drama', 'read', 'comic', 'manga-read'])

export default function App() {
  const route = useRoute()
  const session = useSession()
  const view = gateView(session)
  const developerMode = useDeveloperMode(view === 'app')
  const pcMode = usePcOnly()
  const features = useFeatures()
  const [hidden] = useHiddenNav(session)
  const popOut = isPopOutWindow()
  // A pop-out has no side nav, drawer or phone bar: its header carries the page switcher instead, at every width.
  const wide = useMediaQuery(`(min-width: ${DOCK_MIN_WIDTH}px)`) && !popOut
  const navDock = useNavDock()
  const navPosition = useNavPosition()
  const headerRef = useHeaderHeight()
  const mainRef = useRef<HTMLElement>(null)
  const [menuOpen, setMenuOpen] = useState(false)

  // A link or bookmark to a switched-off area lands on the Library, not a blank page.
  const sourcesOff = !featureOn('sources', features) && (route.name === 'sources' || route.name === 'sources-manage')
  useEffect(() => {
    if (sourcesOff) window.location.replace(routeHref({ name: 'library' }))
  }, [sourcesOff])

  useDocumentTitle(mainRef, `${view}:${route.name}`)

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
  const navContext = { session, pcMode, developerMode, hidden, features }
  // Title workspaces and readers keep the header's Menu button: their bottom edge belongs to their own actions.
  const phoneBar = !wide && !popOut && !BOUNDED_ROUTES.has(route.name)
  // Left is the original layout and adds nothing; on a phone only the bar can move, and only to the top.
  const shellClass = [
    'app-shell',
    wide && 'has-rail',
    wide && navDock !== 'float' && 'is-docked',
    wide && navDock === 'minimized' && 'is-minimized',
    phoneBar && 'has-phone-bar',
    (wide ? navPosition !== 'left' : phoneBar && navPosition === 'top') && `nav-${navPosition}`,
  ].filter(Boolean).join(' ')

  const headerEnd = (
    <div className="header-end">
      <CommandPalette route={route} context={navContext} />
      <JobsMenu />
      <NotificationBell />
      <ReportProblemButton hidden={!wide && !popOut} />
      {(wide || popOut) && <ThemeMenu />}
      <ApiStatus />
      {canSignOut(session) && <UserMenu user={user} />}
    </div>
  )

  const routes = (
    <>
      {route.name === 'library' && <LibraryPage />}
      {route.name === 'library-tools' && <LibraryToolsPage />}
      {route.name === 'drama' && <WorkspaceShell id={route.id} stage={route.stage} />}
      {route.name === 'read' && <ReaderPage key={route.id} id={route.id} page={route.page} />}
      {route.name === 'comic' && <ComicPage key={route.id} id={route.id} page={route.page} />}
      {route.name === 'series' && <SeriesPage key={route.id} id={route.id} tab={route.tab} />}
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
      {route.name === 'make-subtitles' && <MakeSubtitlesPage />}
      {(route.name === 'sources' || route.name === 'sources-manage') && featureOn('sources', features) && (
        <SourcesPage tab={route.name === 'sources' ? route.tab : 'manage'} area={route.name === 'sources-manage' ? route.tab : undefined} />
      )}
      {route.name === 'discover' && <DiscoverPage />}
      {route.name === 'live' && <LivePage />}
      {route.name === 'diagnostics' && <DiagnosticsPage />}
      {route.name === 'assistant' && <AssistantPage />}
      {route.name === 'benchmark' && <BenchmarkPage compare={route.compare} />}
    </>
  )

  // Header and nav stay outside the boundary so a crashed page can still be left.
  const content = (
    <>
      <RemoteHealthBanner />
      <RouteErrorBoundary>
        <Suspense fallback={null}>
          {BOUNDED_ROUTES.has(route.name) ? <div className="app-bounded">{routes}</div> : routes}
        </Suspense>
      </RouteErrorBoundary>
    </>
  )

  // One tree at every width, so crossing 600px keeps the open page (and a playing video) mounted.
  return (
    <JobsProvider>
      {signedOut && <SignInOverlay configured={signInConfigured} />}
      <a className="skip-link" href="#main-content" onClick={skipToContent}>Skip to content</a>
      <div className={shellClass}>
        {wide && <SideNav route={route} context={navContext} />}
        <div className="app-main">
          {popOut ? (
            <header className="app-header" ref={headerRef}>
              <BackButton />
              <PopOutMenu route={route} context={navContext} />
              {headerEnd}
            </header>
          ) : wide ? (
            <header className="app-header" ref={headerRef}>
              <BackButton />
              {headerEnd}
            </header>
          ) : (
            <header className="app-header" ref={headerRef}>
              {!phoneBar && <MenuButton open={menuOpen} onOpen={() => setMenuOpen(true)} />}
              <p className="app-brand">
                <a href={routeHref({ name: 'library' })} aria-label="Baihe Studio: Library">
                  <span aria-hidden="true" className="rail-brand-mark">B</span>
                  <span className="brand-word" aria-hidden="true">Baihe<span className="title-rest"> Studio</span></span>
                </a>
              </p>
              {headerEnd}
            </header>
          )}
          <main id="main-content" ref={mainRef} tabIndex={-1} className="app-content">{content}</main>
        </div>
        {!wide && !popOut && <NavDrawer route={route} context={navContext} open={menuOpen} onOpenChange={setMenuOpen} />}
        <GlobalShortcuts route={route} />
        {phoneBar && <PhoneBar route={route} context={navContext} moreOpen={menuOpen} onMore={() => setMenuOpen(true)} />}
      </div>
    </JobsProvider>
  )
}
