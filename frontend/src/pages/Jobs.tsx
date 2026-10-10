/*
 * #/jobs: every job the server lists for this viewer, filterable, with
 * Cancel, PC-only Delete and a link to the stage that started each one.
 * There is no Retry: re-running means opening the stage and pressing its
 * primary, which keeps the cost and engine checks in one place.
 */
import { Fragment, useEffect, useMemo, useState } from 'react'

import { api } from '../api/client'
import { cancelJob, clearFinishedJobs, deleteJob, forceStopJob } from '../api/jobs'
import { Badge } from '../components/Badge'
import { ButtonLink } from '../components/Button'
import { ConfirmButton } from '../components/ConfirmButton'
import { ErrorBanner } from '../components/ErrorBanner'
import { statusTone } from '../components/labels'
import { buttonClass } from '../components/uiClasses'
import { useJobs, jobsRefusal, useNow } from '../hooks/useJobs'
import { useMediaQuery } from '../hooks/useMediaQuery'
import { usePersistedState } from '../hooks/usePersistedState'
import { PC_ONLY_DELETE_NOTE, usePcOnly } from '../hooks/usePcOnly'
import { REMOTE_ADMIN_NOTE, isRemoteAdmin, useSession } from '../hooks/useSession'
import { routeHref } from '../router'
import { FORCE_STOP_HINT, jobName, offersCancel, offersForceStop, jobOutcomeText, type JobRecord } from '../types/jobs'
import { formatDuration, isActive, isFinished, jobDetail, statusLabel } from './diagnosticsFormat'
import { JobStagesPanel } from './diagnostics/JobStagesPanel'
import { RunSettings } from './jobs/RunSettings'
import {
  KIND_OPTIONS, NO_FILTERS, RANGE_OPTIONS, STATUS_CHIPS, effectiveStatus, filterJobs, hasActiveFilters, jobLinks,
  normalizeFilters, progressPercent, relativeTime, sortJobs, statusCounts,
  type JobFilters, type KindFilter, type RangeFilter, type SortDir, type SortKey,
} from './jobs/jobsFilter'
import './jobs/jobs.css'

// The list is capped until asked for more: the endpoint has no paging yet.
const PAGE = 50

const REFUSED_TEXT: Record<number, string> = {
  404: "This server doesn't list jobs.",
  403: "You can't see jobs here.",
  401: 'Sign in to see jobs.',
}

const fullTime = (sec: number) => new Date(sec * 1000).toLocaleString()

export default function JobsPage() {
  const { jobs, error, polling, reload } = useJobs()
  const session = useSession()
  const remoteAdmin = isRemoteAdmin(session)
  const authOn = session.status === 'ready' && session.me.auth_enabled
  const pc = usePcOnly()
  const phone = useMediaQuery('(max-width: 640px)')
  const [stored, setStored] = usePersistedState<JobFilters>('jobs.filters', NO_FILTERS)
  const filters = useMemo(() => normalizeFilters(stored), [stored])
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir }>({ key: 'default', dir: 'desc' })
  const [limit, setLimit] = useState(PAGE)
  const [titles, setTitles] = useState<ReadonlyMap<number, string>>(new Map())
  const [actionError, setActionError] = useState<unknown>(null)
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set())

  // Titles come from the library list; a failed read just leaves the Title column blank.
  useEffect(() => {
    api.listDramas().then(
      (r) => setTitles(new Map(r.items.map((d) => [d.id, d.title_en || d.title_zh || `#${d.id}`]))),
      () => undefined,
    )
  }, [])

  const all = jobs ?? []
  const anyActive = all.some((j) => isActive(j.status))
  const now = useNow(anyActive)
  const refused = jobsRefusal(error)
  const patch = (p: Partial<JobFilters>) => setStored({ ...filters, ...p })

  const status = effectiveStatus(filters, all)
  const counts = statusCounts(all)
  const matching = sortJobs(filterJobs(all, filters, titles, now), sort.key, sort.dir, now)
  const shown = matching.slice(0, Math.max(limit, matching.filter((j) => isActive(j.status)).length))
  const cancellable = (j: JobRecord) => isActive(j.status) && offersCancel(j, remoteAdmin)
  const finishedCount = counts.finished + counts.failed

  async function run(action: () => Promise<unknown>) {
    try {
      await action()
      setActionError(null)
    } catch (e) {
      setActionError(e)
    }
    reload()
  }
  const toggle = (id: string) =>
    setOpen((cur) => {
      const next = new Set(cur)
      if (!next.delete(id)) next.add(id)
      return next
    })
  const sortBy = (key: 'started' | 'duration') =>
    setSort((cur) => (cur.key === key ? { key, dir: cur.dir === 'desc' ? 'asc' : 'desc' } : { key, dir: 'desc' }))

  const rowProps = {
    now, titles, authOn, pc, open,
    cancellable,
    forceStoppable: (j: JobRecord) => offersForceStop(j, remoteAdmin),
    onToggle: toggle,
    onCancel: (id: string) => void run(() => cancelJob(id)),
    onForceStop: (id: string) => void run(() => forceStopJob(id)),
    onDelete: (id: string) => void run(() => deleteJob(id)),
  }

  return (
    <section className="jobs-page" aria-label="Jobs">
      <header className="page-head">
        <h2>Jobs</h2>
        {jobs && <p className="page-meta">{counts.active} active · {counts.all} in all</p>}
      </header>

      {refused !== null ? (
        <p className="muted" role="status" data-testid="jobs-refused">{REFUSED_TEXT[refused]}</p>
      ) : (
        <>
          {error != null && <ErrorBanner error={error} />}
          <ErrorBanner error={actionError} onDismiss={() => setActionError(null)} />
          {polling && <p className="muted" data-testid="jobs-polling">Updating every 10 seconds.</p>}

          {jobs === null ? (
            <ul className="jobs-skeleton" aria-label="Loading jobs">
              {[0, 1, 2, 3].map((i) => (
                <li key={i}><span className="skeleton-line" /><span className="skeleton-line short" /></li>
              ))}
            </ul>
          ) : all.length === 0 ? (
            <div className="jobs-empty" data-testid="jobs-empty">
              <p className="muted">No jobs yet. Jobs appear here when you transcribe, translate or export.</p>
              <ButtonLink href={routeHref({ name: 'library' })}>Go to Library</ButtonLink>
            </div>
          ) : (
            <>
              <div className="jobs-filters">
                <div className="jobs-chips" role="group" aria-label="Status">
                  {STATUS_CHIPS.map((c) => (
                    <button
                      key={c.id}
                      type="button"
                      className={buttonClass(status === c.id ? 'primary' : 'secondary', 'sm')}
                      aria-pressed={status === c.id}
                      onClick={() => patch({ status: c.id })}
                    >
                      {c.label} <span className="jobs-chip-count">{counts[c.id]}</span>
                    </button>
                  ))}
                </div>
                <label className="jobs-search">
                  <span className="visually-hidden">Search jobs</span>
                  <input type="search" placeholder="Search jobs" value={filters.search} onChange={(e) => patch({ search: e.target.value })} />
                </label>
                <label>
                  <span className="visually-hidden">Kind</span>
                  <select value={filters.kind} onChange={(e) => patch({ kind: e.target.value as KindFilter })}>
                    {KIND_OPTIONS.map((k) => <option key={k.id} value={k.id}>{k.label}</option>)}
                  </select>
                </label>
                <label>
                  <span className="visually-hidden">Time range</span>
                  <select value={filters.range} onChange={(e) => patch({ range: e.target.value as RangeFilter })}>
                    {RANGE_OPTIONS.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
                  </select>
                </label>
                {authOn && (
                  <label className="jobs-mine">
                    <input type="checkbox" checked={filters.mine} onChange={(e) => patch({ mine: e.target.checked })} />
                    Mine
                  </label>
                )}
              </div>

              {matching.length === 0 ? (
                <p className="muted" data-testid="jobs-no-match">
                  No jobs match.{' '}
                  <button type="button" className="link" onClick={() => setStored({ ...NO_FILTERS, status: 'all' })}>
                    Clear filters
                  </button>
                </p>
              ) : phone ? (
                <JobCards jobs={shown} {...rowProps} />
              ) : (
                <JobTable jobs={shown} sort={sort} onSort={sortBy} {...rowProps} />
              )}

              {shown.length < matching.length && (
                <p className="actions" data-testid="jobs-more">
                  <span className="muted">Showing {shown.length} of {matching.length}.</span>
                  <button type="button" className={buttonClass('secondary', 'sm')} onClick={() => setLimit(limit + PAGE)}>
                    Show more
                  </button>
                </p>
              )}
              {hasActiveFilters(filters) && matching.length > 0 && (
                <p className="muted">
                  Filters are on.{' '}
                  <button type="button" className="link" onClick={() => setStored({ ...NO_FILTERS, status: filters.status })}>
                    Clear filters
                  </button>
                </p>
              )}
              {all.some((j) => isActive(j.status) && !cancellable(j)) && (
                <p className="muted" data-testid="remote-admin-jobs-note">{REMOTE_ADMIN_NOTE} That includes cancelling their jobs.</p>
              )}
              {pc !== 'remote' && finishedCount > 0 && (
                <div className="actions" data-testid="jobs-clear">
                  <ConfirmButton
                    name="all finished jobs"
                    label="Delete all finished…"
                    ariaLabel="Delete all finished jobs"
                    confirmLabel={`Confirm delete ${finishedCount} finished`}
                    onConfirm={() => void run(() => clearFinishedJobs())}
                  />
                  <span className="muted">Permanent: removes the finished jobs from the history. Running and queued jobs stay.</span>
                </div>
              )}
              {pc === 'remote' && finishedCount > 0 && <p className="muted">{PC_ONLY_DELETE_NOTE}</p>}
            </>
          )}
        </>
      )}
    </section>
  )
}

interface RowProps {
  now: number
  titles: ReadonlyMap<number, string>
  authOn: boolean
  pc: ReturnType<typeof usePcOnly>
  open: ReadonlySet<string>
  cancellable: (j: JobRecord) => boolean
  forceStoppable: (j: JobRecord) => boolean
  onToggle: (id: string) => void
  onCancel: (id: string) => void
  onForceStop: (id: string) => void
  onDelete: (id: string) => void
}

function ProgressBar({ job }: { job: JobRecord }) {
  const pct = progressPercent(job)
  if (pct === null) return null
  return (
    <span className="jobs-progress">
      <span className="jobs-bar" role="progressbar" aria-label={`Progress of ${jobName(job)}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
        <span style={{ width: `${pct}%` }} />
      </span>
      <span className="jobs-pct">{pct}%</span>
    </span>
  )
}

function StatusCell({ job }: { job: JobRecord }) {
  const outcome = jobOutcomeText(job)
  return (
    <>
      <Badge tone={statusTone(job.status)}>{statusLabel(job.status)}</Badge>
      {job.stale && <Badge tone="warn">Stale</Badge>}
      {job.stalled && !job.stale && <Badge tone="warn">Stalled</Badge>}
      {outcome && isFinished(job.status) && outcome !== statusLabel(job.status) && <div className="muted jobs-outcome">{outcome}</div>}
    </>
  )
}

function JobActions({ job, props, expanded }: { job: JobRecord; props: RowProps; expanded: boolean }) {
  const name = jobName(job)
  const links = jobLinks(job)
  return (
    <div className="jobs-actions">
      {props.cancellable(job) && (
        <button type="button" className={buttonClass('secondary', 'sm')} aria-label={`Cancel ${name}`} onClick={() => props.onCancel(job.job_id)}>
          Cancel
        </button>
      )}
      {props.forceStoppable(job) && (
        <>
          <ConfirmButton name={name} label="Force stop…" verb="force stop" confirmLabel="Confirm force stop" onConfirm={() => props.onForceStop(job.job_id)} />
          <span className="muted">{FORCE_STOP_HINT}</span>
        </>
      )}
      {links.stage && (
        <ButtonLink href={routeHref(links.stage.route)} size="sm" aria-label={`Open ${links.stage.label.toLowerCase()} for ${name}`}>
          Open {links.stage.label.toLowerCase()}
        </ButtonLink>
      )}
      <button type="button" className={buttonClass('ghost', 'sm')} aria-expanded={expanded} aria-label={`Details for ${name}`} onClick={() => props.onToggle(job.job_id)}>
        Details
      </button>
      {props.pc !== 'remote' && isFinished(job.status) && <ConfirmButton name={name} confirmLabel="Confirm delete" onConfirm={() => props.onDelete(job.job_id)} />}
    </div>
  )
}

function Details({ job, now }: { job: JobRecord; now: number }) {
  const detail = jobDetail(job)
  const outcome = jobOutcomeText(job)
  const fields = Object.entries(job.result ?? {}).filter(([, v]) => ['string', 'number', 'boolean'].includes(typeof v))
  return (
    <div className="jobs-details" data-testid={`job-details-${job.job_id}`}>
      <dl>
        <dt>Started</dt>
        <dd>{job.started_at != null ? fullTime(job.started_at) : 'Not started'}</dd>
        {job.finished_at != null && (<><dt>Finished</dt><dd>{fullTime(job.finished_at)}</dd></>)}
        <dt>Duration</dt>
        <dd>{formatDuration(job, now)}</dd>
      </dl>
      {outcome && <p>{outcome}</p>}
      {detail && <p className={job.status === 'error' ? 'error' : 'muted'}>{detail}</p>}
      {fields.length > 0 && (
        <dl>
          {fields.map(([k, v]) => (<span key={k} className="jobs-field"><dt>{k.replace(/_/g, ' ')}</dt><dd>{String(v)}</dd></span>))}
        </dl>
      )}
      <JobStagesPanel jobId={job.job_id} />
      <RunSettings result={job.result} />
    </div>
  )
}

type TableProps = RowProps & { jobs: JobRecord[]; sort: { key: SortKey; dir: SortDir }; onSort: (k: 'started' | 'duration') => void }

function JobTable({ jobs, sort, onSort, ...props }: TableProps) {
  const ariaSort = (k: 'started' | 'duration') => (sort.key === k ? (sort.dir === 'asc' ? 'ascending' : 'descending') : undefined)
  const cols = props.authOn ? 9 : 8
  return (
    <table className="jobs-table" data-testid="jobs-table">
      <thead>
        <tr>
          <th>Job</th>
          <th>Title</th>
          <th>Stage</th>
          <th>Status</th>
          <th>Progress</th>
          <th aria-sort={ariaSort('started')}>
            <button type="button" className="link" onClick={() => onSort('started')}>Started</button>
          </th>
          <th aria-sort={ariaSort('duration')}>
            <button type="button" className="link" onClick={() => onSort('duration')}>Duration</button>
          </th>
          {props.authOn && <th>Who</th>}
          <th>Actions</th>
        </tr>
      </thead>
      <tbody>
        {jobs.map((j) => {
          const links = jobLinks(j)
          const title = j.drama_id != null ? props.titles.get(j.drama_id) ?? `#${j.drama_id}` : null
          const expanded = props.open.has(j.job_id)
          const note = jobDetail(j)
          return (
            <Fragment key={j.job_id}>
              <tr data-testid={`job-row-${j.job_id}`}>
                <td className="jobs-name">
                  <strong>{jobName(j)}</strong>
                  {note && <div className="muted jobs-note">{note}</div>}
                </td>
                <td>{title && links.title ? <a href={routeHref(links.title)}>{title}</a> : ''}</td>
                <td>{links.stage ? <a href={routeHref(links.stage.route)}>{links.stage.label}</a> : ''}</td>
                <td><StatusCell job={j} /></td>
                <td><ProgressBar job={j} /></td>
                <td>{j.started_at != null && <time dateTime={new Date(j.started_at * 1000).toISOString()}>{relativeTime(j.started_at, props.now)}</time>}</td>
                <td>{formatDuration(j, props.now)}</td>
                {props.authOn && <td>{j.owned_by_me ? 'You' : 'Someone else'}</td>}
                <td><JobActions job={j} props={props} expanded={expanded} /></td>
              </tr>
              {expanded && (
                <tr>
                  <td colSpan={cols}><Details job={j} now={props.now} /></td>
                </tr>
              )}
            </Fragment>
          )
        })}
      </tbody>
    </table>
  )
}

function JobCards({ jobs, ...props }: RowProps & { jobs: JobRecord[] }) {
  return (
    <ul className="jobs-cards" data-testid="jobs-cards" aria-label="Jobs">
      {jobs.map((j) => {
        const links = jobLinks(j)
        const title = j.drama_id != null ? props.titles.get(j.drama_id) ?? `#${j.drama_id}` : null
        const expanded = props.open.has(j.job_id)
        const note = jobDetail(j)
        return (
          <li key={j.job_id} data-testid={`job-row-${j.job_id}`}>
            {title && links.title ? <a className="jobs-card-title" href={routeHref(links.title)}>{title}</a> : null}
            <strong>{jobName(j)}</strong>
            <div className="jobs-card-status"><StatusCell job={j} /><span className="muted">{formatDuration(j, props.now)}</span></div>
            <ProgressBar job={j} />
            {note && <p className="muted jobs-note">{note}</p>}
            {props.authOn && <p className="muted">{j.owned_by_me ? 'You' : 'Someone else'}</p>}
            <JobActions job={j} props={props} expanded={expanded} />
            {expanded && <Details job={j} now={props.now} />}
          </li>
        )
      })}
    </ul>
  )
}
