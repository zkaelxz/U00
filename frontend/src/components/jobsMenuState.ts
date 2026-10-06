// Pure helpers for the header Jobs button (JobsMenu.tsx).
import { formatDuration, isActive, isFinished, orderJobs } from '../pages/diagnosticsFormat'
import type { JobRecord } from '../types/jobs'

export const RECENT_FINISHED = 5
export const JOBS_POLL_MS = 10_000
export const HIDE_ON = [401, 403, 404]

export const activeCount = (jobs: JobRecord[]) => jobs.filter((j) => isActive(j.status)).length

// All active jobs, then the newest few finished ones (server order is newest first).
export function menuJobs(jobs: JobRecord[], recent = RECENT_FINISHED): JobRecord[] {
  const ordered = orderJobs(jobs)
  const active = ordered.filter((j) => isActive(j.status))
  return [...active, ...ordered.filter((j) => isFinished(j.status)).slice(0, recent)]
}

export function jobsButtonLabel(count: number): string {
  if (count <= 0) return 'Jobs'
  return `Jobs (${count} ${count === 1 ? 'job' : 'jobs'} running)`
}

// Counts above 99 read "99+" so the badge stays small.
export const badgeText = (count: number) => (count > 99 ? '99+' : String(count))

export const elapsedText = (job: JobRecord, nowSec: number) => formatDuration(job, nowSec)
