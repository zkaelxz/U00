// Pure helpers for the Library's Running line and the "Titles you added" shelf (HomeShelves.tsx; a different name from it, since Windows file names ignore case).

import type { JobRecord } from '../types/jobs'
import type { LibraryRunningEntry } from '../types/library'

/** "2 h 05 m" past an hour, else "45 min" (never "0 min"). */
export function formatLength(seconds: number): string {
  const minutes = Math.max(1, Math.round(seconds / 60))
  if (minutes < 60) return `${minutes} min`
  return `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, '0')} m`
}

const STEP_VERB: Record<string, string> = {
  transcribe: 'Transcribing', translate: 'Translating', export: 'Exporting', autoqc: 'Checking', auto_qc: 'Checking',
}
export const stepVerb = (step: string | null | undefined): string => (step && STEP_VERB[step]) || 'Working on'

export interface RunningLine { text: string; progress: number | null; left: string | null; waiting: string | null }

/** The one Running line: the step that is running (or first in line), and how many wait behind it. */
export function runningLine(entries: LibraryRunningEntry[], total: number, jobs: JobRecord[] | null, now: number): RunningLine | null {
  const first = entries.find((e) => e.status === 'running') ?? entries[0]
  if (!first) return null
  const job = first.job_id ? jobs?.find((j) => j.job_id === first.job_id) : undefined
  const progress = first.status === 'running' && typeof job?.progress === 'number' ? job.progress : null
  const others = total - 1
  return {
    text: first.status === 'running' ? `${stepVerb(first.step)} ${first.title}` : `Next: ${stepVerb(first.step).toLowerCase()} ${first.title}`,
    progress,
    left: timeLeft(progress, job?.started_at ?? null, now),
    waiting: others > 0 ? `${others} more ${others === 1 ? 'is' : 'are'} waiting ${others === 1 ? 'its' : 'their'} turn` : null,
  }
}

/** "18 min left" from how long the step has run and how far it got; null while too early to tell. */
export function timeLeft(progress: number | null, startedAt: number | null, now: number): string | null {
  if (progress === null || startedAt === null || progress < 0.03 || progress >= 1) return null
  const elapsed = now - startedAt
  if (elapsed <= 0) return null
  const left = (elapsed * (1 - progress)) / progress
  return `${formatLength(left)} left`
}
