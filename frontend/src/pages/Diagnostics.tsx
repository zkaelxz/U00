import { useCallback, useEffect, useState } from 'react'

import { getDiagnostics, getJobHistory, getModelCache, getSetupChecks } from '../api/diagnostics'
import { cancelJob, listJobs } from '../api/jobs'
import { Badge } from '../components/Badge'
import { ButtonLink } from '../components/Button'
import { Card } from '../components/Card'
import { ErrorBanner } from '../components/ErrorBanner'
import { statusTone } from '../components/labels'
import { Section } from '../components/Section'
import { buttonClass } from '../components/uiClasses'
import { useEventStream } from '../hooks/useEventStream'
import { useMediaQuery } from '../hooks/useMediaQuery'
import { usePcOnly } from '../hooks/usePcOnly'
import { routeHref } from '../router'
import type {
  DiagnosticsJobHistoryItem, DiagnosticsModelCache, DiagnosticsOverview, DiagnosticsSetupChecks,
} from '../types/diagnostics'
import type { JobRecord } from '../types/jobs'
import { BugBundlesSection } from './diagnostics/BugBundlesSection'
import { BugReportsSection } from './diagnostics/BugReportsSection'
import { DangerZone } from './diagnostics/DangerZone'
import { DenoInstall } from './diagnostics/DenoInstall'
import { JobHistorySection } from './diagnostics/JobHistorySection'
import { LogSection } from './diagnostics/LogSection'
import { ModelCacheSection } from './diagnostics/ModelCacheSection'
import { PackagesSection } from './diagnostics/PackagesSection'
import { PyannoteSection } from './diagnostics/PyannoteSection'
import { SetupSection } from './diagnostics/SetupSection'
import { SupportReportSection } from './diagnostics/SupportReportSection'
import { headerBadges, setupRows, type AdminBusy } from './diagnostics/diagnosticsAdmin'
import './diagnostics/diagnostics.css'
import { formatDuration, isActive, jobDetail, jobStatusLine, splitDependencies, statusLabel, upsertJob } from './diagnosticsFormat'

const POLL_MS = 3000

export default function DiagnosticsPage() {
  const pc = usePcOnly()
  const [overview, setOverview] = useState<DiagnosticsOverview | null>(null)
  const [setup, setSetup] = useState<DiagnosticsSetupChecks | null>(null)
  const [checking, setChecking] = useState(false)
  const [jobs, setJobs] = useState<JobRecord[] | null>(null)
  const [history, setHistory] = useState<DiagnosticsJobHistoryItem[] | null>(null)
  const [cache, setCache] = useState<DiagnosticsModelCache | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [now, setNow] = useState(() => Date.now() / 1000)
  // One install, upgrade or reset at a time; every admin button waits for it.
  const [adminBusy, setAdminBusy] = useState<AdminBusy>(null)
  const [packagesOpen, setPackagesOpen] = useState(false)
  const [dangerOpen, setDangerOpen] = useState(false)

  const refreshJobs = useCallback(async () => {
    try {
      setJobs((await listJobs()).items)
      setNow(Date.now() / 1000)
    } catch (e) {
      setError(e)
    }
  }, [])

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

  const refreshHistory = useCallback(() => {
    getJobHistory().then(setHistory, () => undefined)
  }, [])

  const refreshCache = useCallback(() => {
    getModelCache().then(setCache, () => undefined)
  }, [])

  useEffect(() => {
    refreshSetup()
    refreshHistory()
    refreshCache()
  }, [refreshSetup, refreshHistory, refreshCache])

  // Job changes are pushed (GET /api/events); the list is read once at the
  // start and after every (re)connect.
  const stream = useEventStream((type, data) => {
    const pushed = data as Partial<JobRecord> | null
    if (!pushed?.job_id || (type !== 'job' && type !== 'job_gone')) return
    setJobs((cur) => (cur === null ? cur : type === 'job' ? upsertJob(cur, pushed as JobRecord) : cur.filter((j) => j.job_id !== pushed.job_id)))
    setNow(Date.now() / 1000)
  })
  useEffect(() => {
    listJobs().then((r) => {
      setJobs(r.items)
      setNow(Date.now() / 1000)
    }, setError)
  }, [stream.syncs])

  const active = jobs !== null && jobs.some((j) => isActive(j.status))
  const running = jobs?.filter((j) => isActive(j.status)).length ?? 0
  // While the stream is down: poll while a job runs, and while Packages or
  // the Danger zone is open (their buttons wait for running jobs).
  const watch = stream.mode === 'poll' && (active || packagesOpen || dangerOpen)
  useEffect(() => {
    if (!watch) return
    const t = setInterval(() => void refreshJobs(), POLL_MS)
    return () => clearInterval(t)
  }, [watch, refreshJobs])
  // A job just finished: it moves to the history.
  useEffect(() => {
    if (!active) refreshHistory()
  }, [active, refreshHistory])

  const cancel = async (id: string) => {
    try {
      await cancelJob(id)
      setError(null)
    } catch (e) {
      setError(e)
    }
    await refreshJobs()
  }

  const afterReset = useCallback(() => {
    void refreshJobs()
    refreshHistory()
  }, [refreshJobs, refreshHistory])

  const setupProblems = setup ? setupRows(setup, overview?.gpu ?? null).filter((r) => r.problem).length : null
  const deps = overview ? splitDependencies(overview.dependencies) : null
  // Running or failed jobs get a card at the top; finished ones a fold with the others.
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
        <p className="page-meta">
          <ButtonLink href={routeHref({ name: 'benchmark' })} variant="secondary" size="sm">
            Benchmark Lab
          </ButtonLink>{' '}
          Test engines and prompts against golden sets.
        </p>
      </header>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      {jobs && jobsUrgent && <JobsBlock jobs={jobs} now={now} onCancel={(id) => void cancel(id)} />}

      {setup ? (
        <SetupSection
          checks={setup}
          gpu={overview?.gpu ?? null}
          engines={overview?.model_engine_versions ?? []}
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

      <SupportReportSection />

      <div className="diag-folds">
        {jobs && jobs.length > 0 && !jobsUrgent && <JobsBlock jobs={jobs} now={now} onCancel={(id) => void cancel(id)} />}
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
        <PyannoteSection />
        <ModelCacheSection cache={cache} pc={pc} onChanged={refreshCache} />
        <JobHistorySection items={history} />
        <LogSection />
        <BugReportsSection pc={pc} />
        <BugBundlesSection pc={pc} />
      </div>

      <DangerZone pc={pc} jobsActive={active} busy={adminBusy} onBusy={setAdminBusy} onReset={afterReset} onOpenChange={setDangerOpen} />
    </section>
  )
}

/** Jobs: a card while one is running or failed; otherwise a collapsed Section. */
function JobsBlock({ jobs, now, onCancel }: { jobs: JobRecord[]; now: number; onCancel: (id: string) => void }) {
  const phone = useMediaQuery('(max-width: 640px)')
  const list = phone ? <JobCards jobs={jobs} now={now} onCancel={onCancel} /> : <JobTable jobs={jobs} now={now} onCancel={onCancel} />
  const urgent = jobs.some((j) => isActive(j.status) || j.status === 'error')
  if (urgent) {
    return (
      <Card title="Jobs" className="diag-jobs" aria-label="Jobs">
        {list}
      </Card>
    )
  }
  return (
    <Section title="Jobs" count={jobs.length} storageKey="diagnostics.jobs" summary="None running">
      {list}
    </Section>
  )
}

function JobTable({ jobs, now, onCancel }: { jobs: JobRecord[]; now: number; onCancel: (id: string) => void }) {
  return (
    <div className="table-scroll">
      <table data-testid="job-list">
        <thead>
          <tr>
            <th>Job</th>
            <th>Status</th>
            <th>Time</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {jobs.map((j) => (
            <tr key={j.job_id}>
              <td>{j.description || j.job_id}</td>
              <td>
                <Badge tone={statusTone(j.status)}>{statusLabel(j.status)}</Badge>
                {j.progress != null && isActive(j.status) && ` ${Math.round(j.progress * 100)}%`}
                {jobDetail(j) && <div className="muted">{jobDetail(j)}</div>}
              </td>
              <td>{formatDuration(j, now)}</td>
              <td>
                {isActive(j.status) && (
                  <button type="button" className={buttonClass('secondary', 'sm')} aria-label={`Cancel ${j.description || j.job_id}`} onClick={() => onCancel(j.job_id)}>
                    Cancel
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function JobCards({ jobs, now, onCancel }: { jobs: JobRecord[]; now: number; onCancel: (id: string) => void }) {
  return (
    <ul className="job-cards" data-testid="job-list" aria-label="Jobs">
      {jobs.map((j) => (
        <li key={j.job_id}>
          <strong>{j.description || j.job_id}</strong>
          <p>{jobStatusLine(j, now)}</p>
          {jobDetail(j) && <p className="muted">{jobDetail(j)}</p>}
          {isActive(j.status) && (
            <div className="job-cancel">
              <button type="button" className={buttonClass('secondary', 'sm')} aria-label={`Cancel ${j.description || j.job_id}`} onClick={() => onCancel(j.job_id)}>
                Cancel
              </button>
            </div>
          )}
        </li>
      ))}
    </ul>
  )
}
