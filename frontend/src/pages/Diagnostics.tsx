import { useCallback, useEffect, useState } from 'react'

import { getDiagnostics, getModelCache, getSetupChecks } from '../api/diagnostics'
import { Badge } from '../components/Badge'
import { ButtonLink } from '../components/Button'
import { ErrorBanner } from '../components/ErrorBanner'
import { RemoteHealthLine } from '../components/RemoteHealthBanner'
import { useJobs } from '../hooks/useJobs'
import type { JobRecord } from '../types/jobs'
import { usePcOnly } from '../hooks/usePcOnly'
import { routeHref } from '../router'
import type { DiagnosticsModelCache, DiagnosticsOverview, DiagnosticsSetupChecks } from '../types/diagnostics'
import { DangerZone } from './diagnostics/DangerZone'
import { DenoInstall } from './diagnostics/DenoInstall'
import { LogSection } from './diagnostics/LogSection'
import { ModelHealthCard } from './diagnostics/ModelHealthCard'
import { PackagesSection } from './diagnostics/PackagesSection'
import { PortsSection } from './diagnostics/PortsSection'
import { SetupSection } from './diagnostics/SetupSection'
import { headerBadges, setupRows, type AdminBusy } from './diagnostics/diagnosticsAdmin'
import './diagnostics/diagnostics.css'
import { isActive, jobsSummary, splitDependencies } from './diagnosticsFormat'

const POLL_MS = 3000

export default function DiagnosticsPage() {
  const pc = usePcOnly()
  const { jobs, error: jobsError, polling, reload: refreshJobs } = useJobs()
  const [overview, setOverview] = useState<DiagnosticsOverview | null>(null)
  const [setup, setSetup] = useState<DiagnosticsSetupChecks | null>(null)
  const [checking, setChecking] = useState(false)
  const [cache, setCache] = useState<DiagnosticsModelCache | null>(null)
  const [error, setError] = useState<unknown>(null)
  // One install, upgrade or reset at a time; every admin button waits for it.
  const [adminBusy, setAdminBusy] = useState<AdminBusy>(null)
  const [packagesOpen, setPackagesOpen] = useState(false)
  const [dangerOpen, setDangerOpen] = useState(false)

  const refreshSetup = useCallback(() => {
    const done = () => setChecking(false)
    Promise.all([
      getDiagnostics().then(setOverview),
      getSetupChecks().then(setSetup),
    ]).then(done, (e: unknown) => {
      setError(e)
      done()
    })
  }, [])

  const refreshCache = useCallback(() => {
    getModelCache().then(setCache, () => undefined)
  }, [])

  useEffect(() => {
    refreshSetup()
    refreshCache()
  }, [refreshSetup, refreshCache])

  const active = jobs !== null && jobs.some((j) => isActive(j.status))
  const running = jobs?.filter((j) => isActive(j.status)).length ?? 0
  // While the stream is down: poll while a job runs, and while Packages or
  // the Danger zone is open (their buttons wait for running jobs).
  const watch = polling && (active || packagesOpen || dangerOpen)
  useEffect(() => {
    if (!watch) return
    const t = setInterval(() => void refreshJobs(), POLL_MS)
    return () => clearInterval(t)
  }, [watch, refreshJobs])
  const afterReset = useCallback(() => void refreshJobs(), [refreshJobs])

  const setupProblems = setup ? setupRows(setup, overview?.gpu ?? null).filter((r) => r.problem).length : null
  const deps = overview ? splitDependencies(overview.dependencies) : null
  // Running or failed jobs put the summary at the top as a banner; otherwise it sits with the other folds.
  const jobsUrgent = !!jobs && jobs.some((j) => isActive(j.status) || j.status === 'error')
  const badges = headerBadges(setupProblems, deps?.installed.length ?? null,
    overview ? Object.keys(overview.dependencies).length : null, jobs ? running : null, adminBusy)

  return (
    <section className="page-narrow diag-page" aria-label="Diagnostics">
      <header className="page-head diag-head">
        <h2>Diagnostics</h2>
        <p className="page-meta pill-row" data-testid="diagnostics-summary">
          {badges.length ? badges.map((b) => <Badge key={b.key} tone={b.tone}>{b.text}</Badge>) : 'Loading…'}
        </p>
        <div className="diag-lab-link">
          <ButtonLink href={routeHref({ name: 'benchmark' })} variant="secondary" size="sm">
            Benchmark Lab
          </ButtonLink>
          <p className="page-meta">Test engines and prompts against golden sets.</p>
        </div>
        <RemoteHealthLine />
      </header>
      <ErrorBanner error={error ?? jobsError} onDismiss={() => setError(null)} />

      {jobs && jobsUrgent && <JobsSummary jobs={jobs} urgent />}

      {setup ? (
        <SetupSection
          checks={setup}
          gpu={overview?.gpu ?? null}
          engines={overview?.model_engine_versions ?? []}
          cache={cache}
          pc={pc}
          onCacheChanged={refreshCache}
          checking={checking}
          onRecheck={() => {
            setChecking(true)
            refreshSetup()
          }}
        >
          <DenoInstall pc={pc} jobsActive={active} busy={adminBusy}
            onStarted={() => void refreshJobs()} onFinished={refreshSetup} />
        </SetupSection>
      ) : (
        !error && <p className="muted">Loading…</p>
      )}

      <ModelHealthCard pc={pc} />

      <div className="diag-folds">
        {jobs && jobs.length > 0 && !jobsUrgent && <JobsSummary jobs={jobs} urgent={false} />}
        {overview && (
          <PackagesSection
            overview={overview}
            pc={pc}
            jobsActive={active}
            busy={adminBusy}
            onBusy={setAdminBusy}
            onChanged={refreshSetup}
            onOpenChange={setPackagesOpen}
            onJobStarted={() => void refreshJobs()}
          />
        )}
        <PortsSection pc={pc} />
        <LogSection />
      </div>

      <DangerZone pc={pc} jobsActive={active} busy={adminBusy} onBusy={setAdminBusy} onReset={afterReset} onOpenChange={setDangerOpen} />
    </section>
  )
}

/** One line about jobs and a link to the Jobs page, which holds the table, Cancel, Delete and each job's stage times. */
function JobsSummary({ jobs, urgent }: { jobs: JobRecord[]; urgent: boolean }) {
  return (
    <div className={urgent ? 'banner diag-jobs-summary' : 'diag-jobs-summary'} data-testid="jobs-summary">
      <p>Jobs: {jobsSummary(jobs)}.</p>
      <ButtonLink href={routeHref({ name: 'jobs' })} size="sm">Open Jobs</ButtonLink>
    </div>
  )
}
