import type { DependencyStatus, GpuStatus } from '../types/diagnostics'
import type { JobRecord } from '../types/jobs'

export const isActive = (status: string) => status === 'queued' || status === 'running'

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
  const total = Math.max(0, Math.floor((job.finished_at ?? nowSec) - job.started_at))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  if (h) return `${h}h ${String(m).padStart(2, '0')}m`
  if (m) return `${m}m ${String(s).padStart(2, '0')}s`
  return `${s}s`
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
