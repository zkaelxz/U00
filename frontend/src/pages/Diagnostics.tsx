import { useCallback, useEffect, useState } from 'react'

import { getDiagnostics, getJobHistory, getModelCache, getSetupChecks } from '../api/diagnostics'
import { cancelJob, listJobs } from '../api/jobs'
import { ErrorBanner } from '../components/ErrorBanner'
import { Section } from '../components/Section'
import { useMediaQuery } from '../hooks/useMediaQuery'
import { usePcOnly } from '../hooks/usePcOnly'
import type {
  DiagnosticsJobHistoryItem, DiagnosticsModelCache, DiagnosticsOverview, DiagnosticsSetupChecks,
} from '../types/diagnostics'
import type { JobRecord } from '../types/jobs'
import { BugBundlesSection } from './diagnostics/BugBundlesSection'
import { BugReportsSection } from './diagnostics/BugReportsSection'
import { DangerZone } from './diagnostics/DangerZone'
import { JobHistorySection } from './diagnostics/JobHistorySection'
import { LogSection } from './diagnostics/LogSection'
import { ModelCacheSection } from './diagnostics/ModelCacheSection'
import { PackagesSection } from './diagnostics/PackagesSection'
import { PyannoteSection } from './diagnostics/PyannoteSection'
import { SetupSection } from './diagnostics/SetupSection'
import { SupportReportSection } from './diagnostics/SupportReportSection'
import { headerParts, setupRows, type AdminBusy } from './diagnostics/diagnosticsAdmin'
import './diagnostics/diagnostics.css'
import { formatDuration, isActive, jobStatusLine, splitDependencies, statusLabel } from './diagnosticsFormat'

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
    listJobs().then((r) => setJobs(r.items), setError)
    refreshHistory()
    refreshCache()
  }, [refreshSetup, refreshHistory, refreshCache])

  const active = jobs !== null && jobs.some((j) => isActive(j.status))
  const running = jobs?.filter((j) => isActive(j.status)).length ?? 0
  // Poll while a job runs, and while Packages or the Danger zone is open
  // (their buttons wait for running jobs).
  const watch = active || packagesOpen || dangerOpen
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
  const head = headerParts(setupProblems, deps?.installed.length ?? null, overview ? Object.keys(overview.dependencies).length : null, running, adminBusy)

  return (
    <section className="panel" aria-label="Diagnostics">
      <header className="diag-header">
        <h2>Diagnostics</h2>
        <p className="muted" data-testid="diagnostics-summary">
          {head.setup && <span className={head.warn ? 'warn' : undefined}>{head.setup}</span>}
          {head.setup && head.rest && ' · '}
          {head.rest}
          {!head.setup && !head.rest && 'Loading…'}
        </p>
      </header>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      {/* Core checks first (Python, ffmpeg with libass, JS runtime), then jobs. */}
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
        />
      ) : (
        <p className="muted">Loading…</p>
      )}

      {jobs && jobs.length > 0 && <JobsBlock jobs={jobs} now={now} onCancel={(id) => void cancel(id)} />}

      {overview && (
        <PackagesSection
          overview={overview}
          pc={pc}
          jobsActive={active}
          busy={adminBusy}
          onBusy={setAdminBusy}
          onChanged={refreshSetup}
          onOpenChange={setPackagesOpen}
        />
      )}

      <PyannoteSection />
      <ModelCacheSection cache={cache} pc={pc} onChanged={refreshCache} />
      <JobHistorySection items={history} />
      <LogSection />
      <SupportReportSection />
      <BugReportsSection pc={pc} />
      <BugBundlesSection pc={pc} />

      <DangerZone pc={pc} jobsActive={active} busy={adminBusy} onBusy={setAdminBusy} onReset={afterReset} onOpenChange={setDangerOpen} />
    </section>
  )
}

/** Jobs: always shown while one is running or failed; otherwise a collapsed Section. */
function JobsBlock({ jobs, now, onCancel }: { jobs: JobRecord[]; now: number; onCancel: (id: string) => void }) {
  const phone = useMediaQuery('(max-width: 640px)')
  const list = phone ? <JobCards jobs={jobs} now={now} onCancel={onCancel} /> : <JobTable jobs={jobs} now={now} onCancel={onCancel} />
  const urgent = jobs.some((j) => isActive(j.status) || j.status === 'error')
  if (urgent) {
    return (
      <div className="diag-stack diag-jobs">
        <h3>Jobs</h3>
        {list}
      </div>
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
                {statusLabel(j.status)}
                {j.progress != null && isActive(j.status) && ` ${Math.round(j.progress * 100)}%`}
                {j.message && <div className="muted">{j.message}</div>}
              </td>
              <td>{formatDuration(j, now)}</td>
              <td>
                {isActive(j.status) && (
                  <button type="button" aria-label={`Cancel ${j.description || j.job_id}`} onClick={() => onCancel(j.job_id)}>
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
          {j.message && <p className="muted">{j.message}</p>}
          {isActive(j.status) && (
            <div className="job-cancel">
              <button type="button" aria-label={`Cancel ${j.description || j.job_id}`} onClick={() => onCancel(j.job_id)}>
                Cancel
              </button>
            </div>
          )}
        </li>
      ))}
    </ul>
  )
}
