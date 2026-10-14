import { useCallback, useEffect, useState, type ReactNode } from 'react'

import { getDiagnostics, getModelCache, getSetupChecks } from '../api/diagnostics'
import { PageShell } from '../components/PageShell'
import { Badge } from '../components/Badge'
import { ButtonLink } from '../components/Button'
import { ErrorBanner } from '../components/ErrorBanner'
import { RemoteHealthLine } from '../components/RemoteHealthBanner'
import { Tabs } from '../components/Tabs'
import { seedPageTabFolds } from '../components/sectionStorage'
import { tabButtonId, tabPanelId } from '../components/tabIds'
import { useHolds } from '../hooks/useHolds'
import { useJobs } from '../hooks/useJobs'
import { usePcOnly } from '../hooks/usePcOnly'
import { usePersistedState } from '../hooks/usePersistedState'
import { routeHref, useRoute, type Route } from '../router'
import type { DiagnosticsModelCache, DiagnosticsOverview, DiagnosticsSetupChecks } from '../types/diagnostics'
import { BrowserInstall } from './diagnostics/BrowserInstall'
import { DangerZone } from './diagnostics/DangerZone'
import { CudaRuntimeInstall } from './diagnostics/CudaRuntimeInstall'
import { DenoInstall } from './diagnostics/DenoInstall'
import { LogSection } from './diagnostics/LogSection'
import { ModelHealthCard } from './diagnostics/ModelHealthCard'
import { ModelsList } from './diagnostics/ModelsList'
import { LoadedModelsCard } from './settings/LoadedModelsCard'
import { PackagesSection } from './diagnostics/PackagesSection'
import { PortsSection } from './diagnostics/PortsSection'
import { PyannoteSection } from './diagnostics/PyannoteSection'
import { RecommendedInstalls } from './diagnostics/RecommendedInstalls'
import { RealModelCheck } from './diagnostics/RealModelCheck'
import { SetupSection } from './diagnostics/SetupSection'
import { headerBadges, setupRows, type AdminBusy } from './diagnostics/diagnosticsAdmin'
import {
  CARD_TAB, DIAGNOSTICS_TABS, TAB_FOLD_KEYS, cardAnchorId, isDiagnosticsTab, type DiagnosticsCard, type DiagnosticsTab,
} from './diagnostics/diagnosticsTabs'
import './diagnostics/diagnostics.css'
import { isActive, pollWhenSettled, shouldRefreshGpu, splitDependencies } from './diagnosticsFormat'

const POLL_MS = 3000

// The gate sits outside the page so a member's browser never fires the
// diagnostics calls the server would refuse.
export default function DiagnosticsPage() {
  useState(() => seedPageTabFolds('diagnostics', TAB_FOLD_KEYS))
  if (!useHolds('admin.diagnostics')) {
    return (
      <PageShell width="standard" title="Diagnostics" label="Diagnostics" className="diag-page">
        <p className="muted" data-testid="diagnostics-not-allowed">Only an admin can see this page.</p>
      </PageShell>
    )
  }
  return <DiagnosticsContent />
}

const tabOfRoute = (r: Route): DiagnosticsTab | null =>
  r.name !== 'diagnostics' ? null : r.card ? CARD_TAB[r.card] : (r.tab ?? null)

function DiagnosticsContent() {
  const pc = usePcOnly()
  const route = useRoute()
  const { jobs, error: jobsError, polling, reload: refreshJobs } = useJobs()
  const [overview, setOverview] = useState<DiagnosticsOverview | null>(null)
  const [setup, setSetup] = useState<DiagnosticsSetupChecks | null>(null)
  const [checking, setChecking] = useState(false)
  const [cache, setCache] = useState<DiagnosticsModelCache | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [cacheError, setCacheError] = useState<unknown>(null)
  // One install, upgrade or reset at a time; every admin button waits for it.
  const [adminBusy, setAdminBusy] = useState<AdminBusy>(null)
  const [packagesOpen, setPackagesOpen] = useState(false)
  const [dangerOpen, setDangerOpen] = useState(false)
  const [queuedInstalls, setQueuedInstalls] = useState(0)
  const [modelProblems, setModelProblems] = useState(0)

  const [storedTab, setStoredTab] = usePersistedState<DiagnosticsTab>('diagnostics.tab', 'setup')
  // A link's tab wins over the remembered one from the first render, so the card it names is on screen
  // when the page mounts (Packages scrolls to the missing-transcription box as soon as it loads).
  const [tab, setTabState] = useState<DiagnosticsTab>(() =>
    tabOfRoute(route) ?? (isDiagnosticsTab(storedTab) ? storedTab : 'setup'))
  const selectTab = useCallback((t: DiagnosticsTab) => {
    setTabState(t)
    setStoredTab(t)
  }, [setStoredTab])
  // Each jump to a card bumps the signal, which opens that card's fold and scrolls to it.
  const [target, setTarget] = useState<{ card: DiagnosticsCard; n: number } | null>(null)
  const goTo = useCallback((card: DiagnosticsCard) => {
    selectTab(CARD_TAB[card])
    setTarget((t) => ({ card, n: (t?.n ?? 0) + 1 }))
  }, [selectTab])
  useEffect(() => {
    if (route.name !== 'diagnostics') return
    if (route.card) goTo(route.card)
    else if (route.tab) selectTab(route.tab)
  }, [route, goTo, selectTab])
  useEffect(() => {
    if (!target) return
    const scroll = () => document.getElementById(cardAnchorId(target.card))?.scrollIntoView({ block: 'start' })
    // After the tab's panel is no longer hidden.
    const frame = requestAnimationFrame(() => {
      scroll()
      document.getElementById(cardAnchorId(target.card))?.querySelector('summary')?.focus({ preventScroll: true })
    })
    // Cards above the target (Ports, Log) finish loading after the jump and push it down the page, so keep
    // it in place until the person scrolls or a couple of seconds pass.
    const panel = document.getElementById(tabPanelId('diagnostics', CARD_TAB[target.card]))
    const watcher = panel ? new ResizeObserver(scroll) : null
    if (panel) watcher?.observe(panel)
    const settle = () => watcher?.disconnect()
    const timer = setTimeout(settle, 2000)
    const byPerson = ['wheel', 'touchstart', 'keydown', 'pointerdown'] as const
    for (const ev of byPerson) window.addEventListener(ev, settle, { once: true, passive: true })
    return () => {
      cancelAnimationFrame(frame)
      clearTimeout(timer)
      settle()
      for (const ev of byPerson) window.removeEventListener(ev, settle)
    }
  }, [target])
  const signal = (card: DiagnosticsCard) => (target?.card === card ? target.n : 0)

  const refreshSetup = useCallback(() => {
    const done = () => setChecking(false)
    Promise.all([
      getDiagnostics().then(setOverview),
      getSetupChecks().then(setSetup),
    ]).then(() => {
      setError(null)
      done()
    }, (e: unknown) => {
      setError(e)
      done()
    })
  }, [])

  const refreshCache = useCallback(() => {
    getModelCache().then((c) => { setCache(c); setCacheError(null) }, setCacheError)
  }, [])

  useEffect(() => {
    refreshSetup()
    refreshCache()
  }, [refreshSetup, refreshCache])

  const active = jobs !== null && jobs.some((j) => isActive(j.status))
  const running = jobs?.filter((j) => isActive(j.status)).length ?? 0
  // While the stream is down: poll while a job runs, and while Packages or
  // the Reset library card is open (their buttons wait for running jobs).
  const watch = polling && (active || packagesOpen || dangerOpen)
  useEffect(() => {
    if (!watch) return
    const t = setInterval(() => void refreshJobs(), POLL_MS)
    return () => clearInterval(t)
  }, [watch, refreshJobs])
  // Memory in use changes while a GPU job runs; the overview is re-read only then, and not in a hidden tab.
  const [tabHidden, setTabHidden] = useState(() => document.visibilityState === 'hidden')
  useEffect(() => {
    const onChange = () => setTabHidden(document.visibilityState === 'hidden')
    document.addEventListener('visibilitychange', onChange)
    return () => document.removeEventListener('visibilitychange', onChange)
  }, [])
  const refreshGpu = shouldRefreshGpu(jobs, tabHidden)
  useEffect(() => {
    if (!refreshGpu) return
    return pollWhenSettled(() => getDiagnostics().then(setOverview), POLL_MS)
  }, [refreshGpu])
  const afterReset = useCallback(() => void refreshJobs(), [refreshJobs])

  const setupProblems = setup ? setupRows(setup, overview?.gpu ?? null).filter((r) => r.problem).length : null
  const deps = overview ? splitDependencies(overview.dependencies) : null
  const badges = headerBadges(setupProblems, deps?.installed.length ?? null,
    overview ? Object.keys(overview.dependencies).length : null, jobs ? running : null, adminBusy)

  const attention: Record<DiagnosticsTab, number> = { setup: setupProblems ?? 0, packages: queuedInstalls, models: modelProblems, system: 0 }
  const panel = (id: DiagnosticsTab, children: ReactNode) => (
    <div key={id} id={tabPanelId('diagnostics', id)} role="tabpanel" aria-labelledby={tabButtonId('diagnostics', id)} className="diag-panel" hidden={tab !== id}>
      {children}
    </div>
  )
  // An anchor for deep links; a card that renders nothing (no browser problem to fix) leaves no gap.
  const card = (id: DiagnosticsCard, children: ReactNode) => <div id={cardAnchorId(id)} className="diag-card">{children}</div>

  return (
    <PageShell
      width="standard"
      title="Diagnostics"
      label="Diagnostics"
      className="diag-page"
      meta={(
        <p className="page-meta pill-row" data-testid="diagnostics-summary">
          {badges.length ? badges.map((b) => <Badge key={b.key} tone={b.tone}>{b.text}</Badge>) : 'Loading…'}
        </p>
      )}
      headExtra={<RemoteHealthLine />}
      tabs={(
        <Tabs
          label="Diagnostics"
          idPrefix="diagnostics"
          tabs={DIAGNOSTICS_TABS.map((t) => ({ ...t, attention: attention[t.id] }))}
          active={tab}
          onSelect={selectTab}
        />
      )}
    >
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <ErrorBanner error={jobsError} />
      <ErrorBanner error={cacheError} />

      {panel('setup', (
        <>
        {card('recommended', <RecommendedInstalls setup={setup} openSignal={signal('recommended')} />)}
        {card('setup', setup ? (
          <SetupSection
            checks={setup}
            gpu={overview?.gpu ?? null}
            openSignal={signal('setup')}
            checking={checking}
            onRecheck={() => {
              setChecking(true)
              refreshSetup()
            }}
          />
        ) : (
          !error && <p className="muted">Loading…</p>
        ))}
        </>
      ))}

      {panel('packages', (
        <>
          {card('browser', (
            <BrowserInstall pc={pc} jobsActive={active} busy={adminBusy} openSignal={signal('browser')}
              onStarted={() => void refreshJobs()} onFinished={refreshSetup} />
          ))}
          {card('cuda-runtime', (
            <CudaRuntimeInstall pc={pc} jobsActive={active} busy={adminBusy} openSignal={signal('cuda-runtime')}
              onStarted={() => void refreshJobs()} onFinished={refreshSetup} />
          ))}
          {card('deno', (
            <DenoInstall pc={pc} jobsActive={active} busy={adminBusy} openSignal={signal('deno')}
              onStarted={() => void refreshJobs()} onFinished={refreshSetup} />
          ))}
          {card('packages', overview && (
            <PackagesSection
              overview={overview}
              pc={pc}
              jobsActive={active}
              busy={adminBusy}
              onBusy={setAdminBusy}
              onChanged={refreshSetup}
              onOpenChange={setPackagesOpen}
              onJobStarted={() => void refreshJobs()}
              onQueuedChange={setQueuedInstalls}
              openSignal={signal('packages')}
              pickPackage={route.name === 'diagnostics' ? route.pkg : undefined}
            />
          ))}
          {card('speaker', <PyannoteSection openSignal={signal('speaker')} />)}
        </>
      ))}

      {panel('models', (
        <>
          {card('model-status', <ModelHealthCard pc={pc} onProblems={setModelProblems} openSignal={signal('model-status')} />)}
          {card('models', (
            <ModelsList engines={overview?.model_engine_versions ?? []} cache={cache} pc={pc}
              onChanged={refreshCache} onGoTo={goTo} openSignal={signal('models')} />
          ))}
          {card('loaded-models', <LoadedModelsCard openSignal={signal('loaded-models')} />)}
          {card('test-models', <RealModelCheck pc={pc} jobsActive={active} openSignal={signal('test-models')} />)}
          <p className="muted diag-lab-link">
            Compare engines and prompts on your own test sets.{' '}
            <ButtonLink href={routeHref({ name: 'benchmark' })} variant="secondary" size="sm">Open Benchmark Lab</ButtonLink>
          </p>
        </>
      ))}

      {panel('system', (
        <>
          {card('ports', <PortsSection pc={pc} openSignal={signal('ports')} />)}
          {card('log', <LogSection openSignal={signal('log')} />)}
          {card('reset', (
            <DangerZone pc={pc} jobsActive={active} busy={adminBusy} onBusy={setAdminBusy} onReset={afterReset}
              onOpenChange={setDangerOpen} openSignal={signal('reset')} />
          ))}
        </>
      ))}
    </PageShell>
  )
}
