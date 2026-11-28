import { lazy, Suspense, useEffect, useRef, useState } from 'react'

import { loginHref } from './api/auth'
import { featureOn } from './api/features'
import { useFeatures } from './hooks/useFeatures'
import { useMediaQuery } from './hooks/useMediaQuery'
import { JobsProvider } from './hooks/JobsProvider'
import { gateView, menuUser, recheckSession, sessionExpired, useSession } from './hooks/useSession'
import { useAccountTheme } from './hooks/useAccountTheme'
import { RouteErrorBoundary } from './components/ErrorBoundary'
import { usePersistedState } from './hooks/usePersistedState'
import { VIEW_MODE_PREF, ViewModeContext, asViewMode } from './nav/viewMode'
import { useDeveloperMode } from './pages/assistant/developerMode'
import { BackupHealthBanner } from './components/BackupHealthBanner'
import { PaidLimitBanner } from './components/PaidLimitBanner'
import { RemoteHealthBanner } from './components/RemoteHealthBanner'
import LibraryPage from './pages/Library'
import LoginPage from './pages/Login'
import './pages/login.css'
import { GlobalShortcuts } from './nav/GlobalShortcuts'
import { HeaderEnd } from './nav/HeaderEnd'
import { useDocumentTitle } from './nav/documentTitle'
import { useHiddenNav } from './nav/hiddenNav'
import { BackButton } from './nav/BackButton'
import { MenuButton, NavDrawer } from './nav/NavDrawer'
import { PhoneBar } from './nav/PhoneBar'
import { usePhoneBarWanted } from './nav/phoneBarRequest'
import { PopOutMenu } from './nav/PopOutMenu'
import { isPopOutWindow } from './nav/popOut'
import { DOCK_MIN_WIDTH } from './nav/navItems'
import { useNavDock, useNavPosition } from './nav/navDock'
import { SideNav } from './nav/SideNav'
import { useHeaderHeight } from './nav/useHeaderHeight'
import { usePcOnly } from './hooks/usePcOnly'
import { usePcProfile } from './hooks/usePcProfile'
import { profilePicked } from './api/pcProfile'
import { routeHref, useRoute } from './router'

// Each page is its own chunk, so the first load downloads only the shell and the Library.
const PcProfilePicker = lazy(() => import('./pages/PcProfilePicker'))
const AdminPage = lazy(() => import('./pages/Admin'))
const AssistantPage = lazy(() => import('./pages/Assistant'))
const ComicPage = lazy(() => import('./pages/Comic'))
const ComicEditor = lazy(() => import('./pages/comic/ComicEditor'))
const BenchmarkPage = lazy(() => import('./pages/Benchmark'))
const DiagnosticsPage = lazy(() => import('./pages/Diagnostics'))
const DiscoverPage = lazy(() => import('./pages/Discover'))
const JobsPage = lazy(() => import('./pages/Jobs'))
const LibraryToolsPage = lazy(() => import('./pages/LibraryTools'))
const LivePage = lazy(() => import('./pages/Live'))
const ReaderPage = lazy(() => import('./pages/Reader'))
const FollowAlongPage = lazy(() => import('./pages/follow/FollowAlong'))
const WatchPage = lazy(() => import('./pages/watch/Watch'))
const SavedMangaPage = lazy(() => import('./pages/SavedManga'))
const SavedMangaReader = lazy(() => import('./pages/SavedMangaReader'))
const SeriesPage = lazy(() => import('./pages/series/SeriesPage'))
const SeriesListPage = lazy(() => import('./pages/series/SeriesList'))
const SettingsPage = lazy(() => import('./pages/Settings'))
const SourcesPage = lazy(() => import('./pages/Sources'))
const UpdatesPage = lazy(() => import('./pages/Updates'))
const MakeSubtitlesPage = lazy(() => import('./pages/MakeSubtitlesPage'))
const SharePage = lazy(() => import('./pages/SharePage'))
const TranslatePage = lazy(() => import('./pages/Translate'))
const WorkspaceShell = lazy(() => import('./pages/workspace/WorkspaceShell'))


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
    // Escape twice closes a dialog in Chromium whatever onCancel says, so reopen it: the page beneath must stay hidden.
    <dialog
      ref={ref}
      className="signin-overlay"
      aria-labelledby="signin-overlay-title"
      onCancel={(e) => e.preventDefault()}
      onClose={(e) => {
        const d = e.currentTarget
        if (!d.open && typeof d.showModal === 'function') d.showModal()
      }}
    >
      <section className="panel login-card">
        <h2 id="signin-overlay-title">You've been signed out</h2>
        {configured ? (
          <>
            <p className="muted">Your unsaved work is still here. Sign in in a new tab, then come back to this one.</p>
            <a className="login-button" href={loginHref(returnTo)} target="_blank" rel="noopener noreferrer">
              Sign in with Google
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

// Review's lines sit after the rail, header and toolbar (about 40 Tab stops); the active row is the line list's one stop.
function skipToLines(e: React.MouseEvent) {
  e.preventDefault()
  const lines = document.querySelector<HTMLElement>('li[data-line-id][tabindex="0"]') ?? document.querySelector<HTMLElement>('li[data-line-id]')
  lines?.focus()
}

// Routes that render inside PageShell; the rest (a title's workspace, the readers) manage their own layout and
// are held to the 1200px column by .app-bounded.
const BOUNDED_ROUTES: ReadonlySet<string> = new Set(['drama', 'read', 'comic', 'comic-edit', 'manga-read'])

export default function App() {
  const route = useRoute()
  const session = useSession()
  const view = gateView(session)
  useAccountTheme(menuUser(session)?.id ?? null, session.status === 'ready' ? session.me.preferences : undefined)
  const developerMode = useDeveloperMode(view === 'app')
  const pcMode = usePcOnly()
  const features = useFeatures()
  const profile = usePcProfile()
  const [pickedHere, setPickedHere] = useState(profilePicked)
  const [hidden] = useHiddenNav(session)
  const [storedMode, storeMode] = usePersistedState<string>(VIEW_MODE_PREF, '')
  const viewMode = asViewMode(storedMode)
  const popOut = isPopOutWindow()
  // A pop-out has no side nav, drawer or phone bar: its header carries the page switcher instead, at every width.
  const wide = useMediaQuery(`(min-width: ${DOCK_MIN_WIDTH}px)`) && !popOut
  const menuWanted = usePhoneBarWanted()
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

  const picking = view === 'app' && !!profile.status?.pin_set && !pickedHere
  // The key changes when the page behind these screens appears, so the title is set once <main> exists.
  useDocumentTitle(mainRef, `${view}:${route.name}:${picking ? 'gate' : 'page'}`)

  if (view === 'connecting') {
    return (
      <p className="muted connecting" role="status">
        Connecting…
      </p>
    )
  }
  // With a PIN set, every new tab on the PC asks who is using Baihe first. The server is already
  // on Guest until then, so the lists behind this screen never show a private title.
  if (picking) {
    return (
      <Suspense fallback={null}>
        <PcProfilePicker onDone={() => { setPickedHere(true); window.location.reload() }} />
      </Suspense>
    )
  }
  const signInConfigured = session.status !== 'ready' || session.me.sign_in_configured
  const signedOut = sessionExpired(session)
  if (view === 'login' && !signedOut) return <LoginPage configured={signInConfigured} />
  const navContext = { session, pcMode, developerMode, hidden, features, simple: viewMode === 'simple' }
  // Title workspaces and readers keep the header's Menu button: their bottom edge belongs to their own actions. The one
  // exception is a page that makes room for the menu under its own bar and asks for it.
  const phoneBar = !wide && !popOut && (!BOUNDED_ROUTES.has(route.name) || (route.name === 'drama' && menuWanted))
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
    <HeaderEnd route={route} context={navContext} room={wide || popOut} session={session} viewMode={viewMode} onViewMode={storeMode} />
  )

  const routes = (
    <>
      {route.name === 'library' && <LibraryPage />}
      {route.name === 'library-tools' && <LibraryToolsPage />}
      {route.name === 'drama' && <WorkspaceShell id={route.id} stage={route.stage} />}
      {route.name === 'read' && <ReaderPage key={route.id} id={route.id} page={route.page} />}
      {route.name === 'follow' && <FollowAlongPage key={route.id} id={route.id} />}
      {route.name === 'watch' && <WatchPage key={route.id} id={route.id} party={route.party} />}
      {route.name === 'comic' && <ComicPage key={route.id} id={route.id} page={route.page} />}
      {route.name === 'comic-edit' && <ComicEditor key={route.id} id={route.id} page={route.page} />}
      {route.name === 'series-list' && <SeriesListPage key={route.streamer === undefined ? 'top' : `streamer:${route.streamer}`} streamer={route.streamer} />}
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
      {route.name === 'make-subtitles' && <MakeSubtitlesPage key={route.url ?? ''} link={route.url ?? null} />}
      {route.name === 'share' && <SharePage url={route.url ?? null} />}
      {(route.name === 'sources' || route.name === 'sources-manage') && featureOn('sources', features) && (
        <SourcesPage tab={route.name === 'sources' ? route.tab : 'manage'} sharedUrl={route.name === 'sources' ? route.url : undefined} openSeries={route.name === 'sources' ? route.open : undefined} area={route.name === 'sources-manage' ? route.tab : undefined} />
      )}
      {route.name === 'discover' && <DiscoverPage />}
      {route.name === 'live' && <LivePage initialUrl={route.url} />}
      {route.name === 'updates' && <UpdatesPage />}
      {route.name === 'diagnostics' && <DiagnosticsPage />}
      {route.name === 'assistant' && <AssistantPage />}
      {route.name === 'benchmark' && <BenchmarkPage compare={route.compare} />}
    </>
  )

  // Header and nav stay outside the boundary so a crashed page can still be left.
  const content = (
    <>
      <RemoteHealthBanner />
      <BackupHealthBanner />
      <PaidLimitBanner />
      <RouteErrorBoundary>
        <Suspense fallback={null}>
          {BOUNDED_ROUTES.has(route.name) ? <div className="app-bounded">{routes}</div> : routes}
        </Suspense>
      </RouteErrorBoundary>
    </>
  )

  // One tree at every width, so crossing 600px keeps the open page (and a playing video) mounted.
  return (
    <ViewModeContext.Provider value={viewMode}>
    <JobsProvider>
      {signedOut && <SignInOverlay configured={signInConfigured} />}
      <a className="skip-link" href="#main-content" onClick={skipToContent}>Skip to content</a>
      {route.name === 'drama' && route.stage === 'review' && <a className="skip-link" href="#main-content" onClick={skipToLines}>Skip to lines</a>}
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
              {/* The Series folders page has its own Library / Series breadcrumb; a second Back beside it says the same thing. */}
              {route.name !== 'series-list' && <BackButton />}
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
        {!wide && !popOut && <NavDrawer route={route} context={navContext} open={menuOpen} onOpenChange={setMenuOpen} viewMode={viewMode} onViewMode={storeMode} />}
        <GlobalShortcuts route={route} />
        {phoneBar && <PhoneBar route={route} context={navContext} moreOpen={menuOpen} onMore={() => setMenuOpen(true)} words={BOUNDED_ROUTES.has(route.name)} />}
      </div>
    </JobsProvider>
    </ViewModeContext.Provider>
  )
}
