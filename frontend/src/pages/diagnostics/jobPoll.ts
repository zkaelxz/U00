// Shared bits for the Diagnostics actions that run as server jobs
// (Deno install, "Test first"): is the job still going, and its progress line.
import type { DiagnosticsJobState } from '../../types/diagnosticsInstalls'

export const JOB_POLL_MS = 2000

export const jobRunning = (job: DiagnosticsJobState | null | undefined): boolean =>
  !!job && (job.status === 'running' || job.status === 'queued')

/** "42% · Downloading Deno v2.9.7..." (the message alone before any progress). */
export function jobProgressLine(job: DiagnosticsJobState): string {
  const pct = Math.round(Math.max(0, Math.min(1, job.progress || 0)) * 100)
  const msg = job.message?.trim() || 'Starting…'
  return pct > 0 ? `${pct}% · ${msg}` : msg
}
