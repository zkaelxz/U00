import { useCallback, useEffect, useState } from 'react'

import { getDiagnostics, getJobHistory, getModelCache, getSetupChecks } from '../api/diagnostics'
import { cancelJob, clearFinishedJobs, deleteJob, listJobs } from '../api/jobs'
import { Badge } from '../components/Badge'
import { ButtonLink } from '../components/Button'
import { ConfirmButton } from '../components/ConfirmButton'
import { ErrorBanner } from '../components/ErrorBanner'
import { RemoteHealthLine } from '../components/RemoteHealthBanner'
import { statusTone } from '../components/labels'
import { Section } from '../components/Section'
import { buttonClass } from '../components/uiClasses'
import { useEventStream } from '../hooks/useEventStream'
import { useMediaQuery } from '../hooks/useMediaQuery'
import { PC_ONLY_DELETE_NOTE, usePcOnly, type PcMode } from '../hooks/usePcOnly'
import { REMOTE_ADMIN_NOTE, isRemoteAdmin, useSession } from '../hooks/useSession'
import { routeHref } from '../router'
import type {
  DiagnosticsJobHistoryItem, DiagnosticsModelCache, DiagnosticsOverview, DiagnosticsSetupChecks,
} from '../types/diagnostics'
import { offersCancel, type JobRecord } from '../types/jobs'
import { DangerZone } from './diagnostics/DangerZone'
import { DenoInstall } from './diagnostics/DenoInstall'
import { JobHistorySection } from './diagnostics/JobHistorySection'
import { LogSection } from './diagnostics/LogSection'
import { ModelHealthCard } from './diagnostics/ModelHealthCard'
import { ModelCacheSection } from './diagnostics/ModelCacheSection'
import { PackagesSection } from './diagnostics/PackagesSection'
import { PortsSection } from './diagnostics/PortsSection'
import { PyannoteSection } from './diagnostics/PyannoteSection'
import { SetupSection } from './diagnostics/SetupSection'
import { headerBadges, setupRows, type AdminBusy } from './diagnostics/diagnosticsAdmin'
import './diagnostics/diagnostics.css'
import { formatDuration, isActive, isFinished, jobDetail, jobStatusLine, jobsSummary, JOBS_PAGE_SIZE, orderJobs, splitDependencies, statusLabel, upsertJob, visibleJobs } from './diagnosticsFormat'

const POLL_MS = 3000

export default function DiagnosticsPage() {
  const pc = usePcOnly()
  const remoteAdmin = isRemoteAdmin(useSession())
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
  // Elapsed times keep counting while a job runs, with or without polls.
  useEffect(() => {
    if (!active) return
    const t = setInterval(() => setNow(Date.now() / 1000), POLL_MS)
    return () => clearInterval(t)
  }, [active])
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

  // Permanent: the record is gone from the history for good.
  const removeJobs = async (id: string | null) => {
    try {
      if (id === null) await clearFinishedJobs()
      else await deleteJob(id)
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
  // Running or failed jobs put the Jobs fold at the top (open by default); otherwise it sits with the other folds.
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
        <RemoteHealthLine />
      </header>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      {jobs && jobsUrgent && <JobsBlock jobs={jobs} now={now} remoteAdmin={remoteAdmin} pc={pc} urgent={jobsUrgent} onCancel={(id) => void cancel(id)} onDelete={(id) => void removeJobs(id)} />}

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

      <ModelHealthCard pc={pc} />

      <div className="diag-folds">
        {jobs && jobs.length > 0 && !jobsUrgent && <JobsBlock jobs={jobs} now={now} remoteAdmin={remoteAdmin} pc={pc} urgent={jobsUrgent} onCancel={(id) => void cancel(id)} onDelete={(id) => void removeJobs(id)} />}
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
        <PortsSection pc={pc} />
        <ModelCacheSection cache={cache} pc={pc} onChanged={refreshCache} />
        <JobHistorySection items={history} />
        <LogSection />
      </div>

      <DangerZone pc={pc} jobsActive={active} busy={adminBusy} onBusy={setAdminBusy} onReset={afterReset} onOpenChange={setDangerOpen} />
    </section>
  )
}

/** Jobs: a fold (open by default while one is running or failed) showing the newest few, active first. */
type JobsBlockProps = { jobs: JobRecord[]; now: number; remoteAdmin: boolean; pc: PcMode; urgent: boolean; onCancel: (id: string) => void; onDelete: (id: string | null) => void }

function JobsBlock({ jobs, now, remoteAdmin, pc, urgent, onCancel, onDelete }: JobsBlockProps) {
  const phone = useMediaQuery('(max-width: 640px)')
  const [limit, setLimit] = useState(JOBS_PAGE_SIZE)
  const cancellable = (j: JobRecord) => isActive(j.status) && offersCancel(j, remoteAdmin)
  const deletable = (j: JobRecord) => pc !== 'remote' && isFinished(j.status)
  const ordered = orderJobs(jobs)
  const shown = visibleJobs(ordered, limit)
  const props = { jobs: shown, now, cancellable, deletable, onCancel, onDelete }
  const body = phone ? <JobCards {...props} /> : <JobTable {...props} />
  const finished = jobs.filter((j) => isFinished(j.status)).length
  return (
    <Section title="Jobs" count={jobs.length} storageKey="diagnostics.jobs" summary={jobsSummary(jobs)} defaultOpen={urgent}>
      {body}
      {shown.length < ordered.length && (
        <p className="actions" data-testid="jobs-more">
          <span className="muted">Showing {shown.length} of {ordered.length}.</span>
          <button type="button" className={buttonClass('secondary', 'sm')} onClick={() => setLimit(limit + JOBS_PAGE_SIZE)}>
            Show {Math.min(JOBS_PAGE_SIZE, ordered.length - shown.length)} more
          </button>
          <button type="button" className={buttonClass('secondary', 'sm')} onClick={() => setLimit(ordered.length)}>
            Show all
          </button>
        </p>
      )}
      {jobs.some((j) => isActive(j.status) && !cancellable(j)) && <p className="muted" data-testid="remote-admin-jobs-note">{REMOTE_ADMIN_NOTE} That includes cancelling their jobs.</p>}
      {pc !== 'remote' && finished > 0 && (
        <div className="actions" data-testid="jobs-clear">
          <ConfirmButton name="all finished jobs" label="Delete all finished…" ariaLabel="Delete all finished jobs" confirmLabel={`Confirm delete ${finished} finished`} onConfirm={() => onDelete(null)} />
          <span className="muted">Permanent: removes the finished jobs from the history. Running and queued jobs stay.</span>
        </div>
      )}
      {pc === 'remote' && finished > 0 && <p className="muted">{PC_ONLY_DELETE_NOTE}</p>}
    </Section>
  )
}

type JobListProps = { jobs: JobRecord[]; now: number; cancellable: (j: JobRecord) => boolean; deletable: (j: JobRecord) => boolean; onCancel: (id: string) => void; onDelete: (id: string | null) => void }

function JobTable({ jobs, now, cancellable, deletable, onCancel, onDelete }: JobListProps) {
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
              <td className="job-name">{j.description || j.job_id}</td>
              <td>
                <Badge tone={statusTone(j.status)}>{statusLabel(j.status)}</Badge>
                {j.progress != null && isActive(j.status) && ` ${Math.round(j.progress * 100)}%`}
                {jobDetail(j) && <div className="muted job-detail">{jobDetail(j)}</div>}
              </td>
              <td>{formatDuration(j, now)}</td>
              <td>
                {cancellable(j) && (
                  <button type="button" className={buttonClass('secondary', 'sm')} aria-label={`Cancel ${j.description || j.job_id}`} onClick={() => onCancel(j.job_id)}>
                    Cancel
                  </button>
                )}
                {deletable(j) && <ConfirmButton name={j.description || j.job_id} onConfirm={() => onDelete(j.job_id)} />}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function JobCards({ jobs, now, cancellable, deletable, onCancel, onDelete }: JobListProps) {
  return (
    <ul className="job-cards" data-testid="job-list" aria-label="Jobs">
      {jobs.map((j) => (
        <li key={j.job_id}>
          <strong>{j.description || j.job_id}</strong>
          <p>{jobStatusLine(j, now)}</p>
          {jobDetail(j) && <p className="muted job-detail">{jobDetail(j)}</p>}
          {cancellable(j) && (
            <div className="job-cancel">
              <button type="button" className={buttonClass('secondary', 'sm')} aria-label={`Cancel ${j.description || j.job_id}`} onClick={() => onCancel(j.job_id)}>
                Cancel
              </button>
            </div>
          )}
          {deletable(j) && (
            <div className="job-cancel">
              <ConfirmButton name={j.description || j.job_id} onConfirm={() => onDelete(j.job_id)} />
            </div>
          )}
        </li>
      ))}
    </ul>
  )
}
