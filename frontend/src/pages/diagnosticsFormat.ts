import type { DependencyStatus, GpuStatus } from '../types/diagnostics'
import { formatLeft, isNoPercentStage } from './workspace/stages/transcribeEstimate'
import { cancellingText, isCancellingMessage, jobName, shownStatus, type JobRecord } from '../types/jobs'

export const isActive = (status: string) => status === 'queued' || status === 'running'

export const isFinished = (status: string) => status === 'done' || status === 'error' || status === 'cancelled'

// Queued/running jobs first (they are what the viewer is waiting on), then
// the rest, each group keeping the server's newest-first order.
export function orderJobs(jobs: JobRecord[]): JobRecord[] {
  return [...jobs.filter((j) => isActive(j.status)), ...jobs.filter((j) => !isActive(j.status))]
}

export const hasActiveJobs = (jobs: JobRecord[]) => jobs.some((j) => isActive(j.status))

export function splitDependencies(deps: Record<string, DependencyStatus>) {
  const rows = Object.entries(deps)
    .map(([name, d]) => ({ name, ...d }))
    .sort((a, b) => a.name.localeCompare(b.name))
  return { installed: rows.filter((r) => r.installed), missing: rows.filter((r) => !r.installed) }
}

export const GPU_MEMORY_LABEL = 'GPU memory in use (all programs)'

export function describeGpu(gpu: GpuStatus): string {
  if (!gpu.available) return gpu.message || 'No GPU detected.'
  const name = gpu.name ?? 'GPU available'
  if (gpu.vram_used_gb == null || gpu.vram_total_gb == null) {
    return gpu.message ? `${name} (${gpu.message})` : name
  }
  return `${name} (${GPU_MEMORY_LABEL}: ${gpu.vram_used_gb.toFixed(1)} / ${gpu.vram_total_gb.toFixed(1)} GB)`
}

// The memory reading moves while a GPU job runs, so the page re-reads it then;
// a hidden tab or an idle GPU has nothing new to show.
export const shouldRefreshGpu = (jobs: JobRecord[] | null, hidden: boolean) =>
  !hidden && jobs !== null && jobs.some((j) => isActive(j.status) && j.gpu_touching)

// Waits `ms` after each request settles rather than on a fixed beat: overlapping
// /api/diagnostics reads race on the library write check and can show a false
// "library not writable". Returns the stop function.
export function pollWhenSettled(tick: () => Promise<unknown>, ms: number): () => void {
  let stopped = false
  let timer: ReturnType<typeof setTimeout> | undefined
  const schedule = () => {
    timer = setTimeout(() => {
      tick().catch(() => {}).finally(() => { if (!stopped) schedule() })
    }, ms)
  }
  schedule()
  return () => {
    stopped = true
    clearTimeout(timer)
  }
}

// Started/finished are epoch seconds. Returns e.g. "42s", "3m 05s", "1h 02m".
export function formatJobDuration(job: JobRecord, nowSec: number): string {
  if (job.started_at == null) return 'not started'
  return formatSeconds((job.finished_at ?? nowSec) - job.started_at)
}

// Too little progress or time makes the straight-line guess swing wildly, so
// no estimate is shown until both are past these.
const ETA_MIN_PROGRESS = 0.05
const ETA_MIN_ELAPSED = 30

// "about N min left" for a running job, from its elapsed time and progress;
// null when there is nothing trustworthy to say. Queued jobs, stalled jobs and
// stages that report no percent of their own (model load) get none.
export function jobTimeLeftText(job: JobRecord, nowSec: number): string | null {
  const p = job.progress
  if (job.status !== 'running' || job.stalled || job.started_at == null || p == null) return null
  if (p < ETA_MIN_PROGRESS || p >= 1 || isNoPercentStage(job.message)) return null
  const elapsed = nowSec - job.started_at
  if (elapsed < ETA_MIN_ELAPSED) return null
  return formatLeft((elapsed * (1 - p)) / p)
}

// A length in seconds as "42s", "3m 05s", "1h 02m".
export function formatSeconds(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  if (h) return `${h}h ${String(m).padStart(2, '0')}m`
  if (m) return `${m}m ${String(s).padStart(2, '0')}s`
  return `${s}s`
}

// The small line under a job's status: live progress text while it runs,
// the error for a failed job, nothing once it is done or cancelled (its
// last progress text, e.g. "Transcribing... 99%", would read as stuck),
// except why a cancelled job ended when the server says (Baihe restarted).
export function jobDetail(job: Pick<JobRecord, 'status' | 'message' | 'error'> & { job_id?: string }): string | null {
  if (job.status === 'cancelled') return job.error || null
  if (job.status === 'done') return null
  if (job.status === 'error') return job.error || job.message || null
  if (isCancellingMessage(job.message) && job.job_id) return cancellingText(job.job_id)
  return job.message || null
}

export function jobStatusText(status: string): string {
  const labels: Record<string, string> = {
    queued: 'Queued',
    running: 'Running',
    done: 'Done',
    error: 'Failed',
    cancelled: 'Cancelled',
  }
  return labels[status] ?? status
}

// A pushed job record (GET /api/events) replaces its row, or a new job goes
// first (the list is newest-started first).
export function upsertJob(list: JobRecord[], job: JobRecord): JobRecord[] {
  const i = list.findIndex((j) => j.job_id === job.job_id)
  if (i < 0) return [job, ...list]
  const next = list.slice()
  next[i] = job
  return next
}

/** The status pill text for a job: Failed for a done job whose outcome failed. */
// A job closed because Baihe restarted is stored as cancelled, but nobody cancelled it.
export const jobStatusLabel = (job: Pick<JobRecord, 'status' | 'outcome'>) =>
  job.outcome === 'interrupted' ? 'Interrupted' : jobStatusText(shownStatus(job))

const PACKAGES_HOME = 'Diagnostics > Packages and installs'
const PIP_INSTALL = /(\b[Rr]un )?`?(?:python -m )?pip3? install((?: -{1,2}[\w-]+)*)((?: ["']?[\w.\-[\]<>=!~,]+["']?)+)`?/g

/**
 * Server text in a person's words: a `pip install x` instruction points at
 * Diagnostics instead (people install through the app), and a job text says
 * title, not drama.
 */
export function plainJobText(text: string): string {
  return text
    .replace(PIP_INSTALL, (_all, run: string | undefined, _flags: string, args: string) => {
      const name = args.trim().split(/\s+/)[0]?.replace(/["']/g, '').split(/[<>=!~[,]/)[0]
      const where = name ? `${PACKAGES_HOME} (${name})` : PACKAGES_HOME
      return run ? `${run.startsWith('R') ? 'Open' : 'open'} ${where}` : where
    })
    .replace(/\bDramas\b/g, 'Titles')
    .replace(/\bdramas\b/g, 'titles')
    .replace(/\bDrama\b/g, 'Title')
    .replace(/\bdrama\b/g, 'title')
}

/** A job's name without its trailing "(title #3)": the Title column already names the title. */
export function jobBaseName(job: Pick<JobRecord, 'description' | 'job_id'>): string {
  const name = plainJobText(jobName(job)).replace(/\s*\((?:title|drama) #\d+\)\s*$/i, '').trim()
  return name || plainJobText(jobName(job))
}

/**
 * The base name plus the title's name where there is room for only one label (the header menu).
 * While the title's name is unknown (not loaded yet, or the read failed) the "(title #3)" suffix
 * stays, so two jobs of one kind never read the same.
 */
export function jobTitledName(job: Pick<JobRecord, 'description' | 'job_id' | 'drama_id'>, titles: ReadonlyMap<number, string>): string {
  const base = jobBaseName(job)
  const title = job.drama_id != null ? titles.get(job.drama_id) : undefined
  if (!title) return plainJobText(jobName(job))
  return base.includes(title) ? base : `${base} - ${title}`
}
