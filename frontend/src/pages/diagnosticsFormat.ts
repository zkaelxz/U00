import type { DependencyStatus, GpuStatus } from '../types/diagnostics'
import type { JobRecord } from '../types/jobs'

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

export function describeGpu(gpu: GpuStatus): string {
  if (!gpu.available) return gpu.message || 'No GPU detected.'
  const vram =
    gpu.vram_used_gb != null && gpu.vram_total_gb != null
      ? ` (${gpu.vram_used_gb.toFixed(1)} / ${gpu.vram_total_gb.toFixed(1)} GB in use)`
      : ''
  return `${gpu.name ?? 'GPU available'}${vram}`
}

// Started/finished are epoch seconds. Returns e.g. "42s", "3m 05s", "1h 02m".
export function formatDuration(job: JobRecord, nowSec: number): string {
  if (job.started_at == null) return 'not started'
  return formatSeconds((job.finished_at ?? nowSec) - job.started_at)
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
export function jobDetail(job: Pick<JobRecord, 'status' | 'message' | 'error'>): string | null {
  if (job.status === 'cancelled') return job.error || null
  if (job.status === 'done') return null
  if (job.status === 'error') return job.error || job.message || null
  return job.message || null
}

export function statusLabel(status: string): string {
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
