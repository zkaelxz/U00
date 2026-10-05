// Pure logic for the workspace job pill (JobPill.tsx, useDramaJobs.ts).
import { isActive, isFinished } from '../diagnosticsFormat'
import { jobFailed, type JobKind, type JobRecord } from '../../types/jobs'

export const FLASH_MS = 5000

const VERBS: Record<JobKind, string> = {
  transcribe: 'Transcribing',
  translate: 'Translating',
  align: 'Aligning',
  dub: 'Dubbing',
  export: 'Exporting',
  review: 'Reviewing',
  import: 'Importing',
  other: 'Working',
}

export const jobVerb = (kind: string | undefined): string => VERBS[kind as JobKind] ?? VERBS.other

export const jobPercent = (progress: number | null | undefined): number | null =>
  progress == null || Number.isNaN(progress) ? null : Math.round(Math.min(Math.max(progress, 0), 1) * 100)

// Queued and running jobs on this title; a job from another title (or one the
// server could not tie to a title) never shows here.
export const activeJobsFor = (jobs: JobRecord[], dramaId: number): JobRecord[] =>
  jobs.filter((j) => j.drama_id === dramaId && isActive(j.status))

// "Translating 42%", or "2 jobs" when several run; null when none.
export function pillText(active: JobRecord[]): string | null {
  if (active.length === 0) return null
  if (active.length > 1) return `${active.length} jobs`
  const [job] = active
  const pct = jobPercent(job.progress)
  return pct === null ? jobVerb(job.kind) : `${jobVerb(job.kind)} ${pct}%`
}

export type PillFlash = 'done' | 'failed'

// What the pill flashes once jobs that were active on the last read are no
// longer active. A job that vanished from the list counts as done; failure
// wins when several finish together. Cancelled reads as Failed: the pill
// has only the two outcomes.
export function finishedFlash(prevActiveIds: ReadonlySet<string>, jobs: JobRecord[], dramaId: number): PillFlash | null {
  const nowActive = new Set(activeJobsFor(jobs, dramaId).map((j) => j.job_id))
  const ended = [...prevActiveIds].filter((id) => !nowActive.has(id))
  if (ended.length === 0) return null
  const failed = ended.some((id) => {
    const rec = jobs.find((j) => j.job_id === id)
    return rec !== undefined && isFinished(rec.status) && (jobFailed(rec) || rec.status === 'cancelled')
  })
  return failed ? 'failed' : 'done'
}
