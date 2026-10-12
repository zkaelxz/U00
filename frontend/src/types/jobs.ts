// Mirrors api/schemas/system.py JobRecord / JobListResponse / JobCancelResult.

export type JobStatus = 'queued' | 'running' | 'done' | 'error' | 'cancelled'

export const TERMINAL_STATUSES: readonly string[] = ['done', 'error', 'cancelled']

export interface JobRecord {
  job_id: string
  status: JobStatus | string
  progress: number | null
  message: string
  error: string | null
  description: string | null
  gpu_touching: boolean
  started_at: number | null
  finished_at: number | null
  updated_at: number
  // Redacted, allowlisted projection of the job's result dict.
  result?: Record<string, unknown> | null
  // Normalised outcome of a finished job (null while queued/running).
  outcome?: JobOutcome | null
  outcome_message?: string | null
  // Queued/running on record but not heartbeated for 15 minutes (judged on
  // the server's clock): left behind by a process that died.
  stale?: boolean
  // Running here, but no progress update for a while (advisory only).
  stalled?: boolean
  // Cancel was heard over a minute ago and the worker still runs: when Force
  // stop is offered. Judged on the server's clock; missing counts as false.
  can_force_stop?: boolean
  // The caller started this job or owns its drama, judged by the server from
  // the session. Missing (an older server) counts as false.
  owned_by_me?: boolean
  // The title a drama-scoped job runs on, else null. Missing (an older
  // server) counts as null.
  drama_id?: number | null
  // What the job does; `other` for anything unclassified. Missing counts as other.
  kind?: JobKind
  // The page the job belongs to, decided by the server from the job id; how a
  // job with no title can still link somewhere. Missing counts as null.
  page?: JobPage | null
}

export type JobPage = 'title' | 'sources' | 'discover' | 'live' | 'settings' | 'diagnostics'

export type JobKind =
  | 'transcribe' | 'translate' | 'align' | 'dub' | 'export' | 'review' | 'import' | 'other'

// A remote household admin may cancel only their own jobs; everyone else
// gets Cancel as before (the server still refuses what they may not stop).
/** The one label for a job in the header menu, the Jobs page and the stage panel. */
export const jobName = (j: Pick<JobRecord, 'description' | 'job_id'>) => j.description || j.job_id

export function offersCancel(job: Pick<JobRecord, 'owned_by_me'>, remoteAdmin: boolean): boolean {
  return !remoteAdmin || job.owned_by_me === true
}

export type JobOutcome = 'ok' | 'failed' | 'cancelled' | 'interrupted' | 'partial' | 'kept_existing'

// A terminal job that did not fully succeed, even when status is 'done'
// (a transcription that returned early with a failure reason, say).
export function jobFailed(job: Pick<JobRecord, 'status' | 'outcome'>): boolean {
  return job.status === 'error' || job.outcome === 'failed' || job.outcome === 'cancelled' || job.outcome === 'interrupted'
}

// The status a person should read. The server calls a job `done` when its
// worker returned, even when the work failed (a missing model, say); the
// outcome says what happened, so a done job with a failed or cancelled
// outcome reads as Failed or Cancelled in every list, pill and filter.
export function shownStatus(job: Pick<JobRecord, 'status' | 'outcome'>): string {
  if (job.status !== 'done' || !jobFailed(job)) return job.status
  return job.outcome === 'cancelled' ? 'cancelled' : 'error'
}

// Done and not failed/cancelled: safe to treat the job's output as ready.
export function jobSucceeded(job: Pick<JobRecord, 'status' | 'outcome'> | null | undefined): boolean {
  return !!job && job.status === 'done' && !jobFailed(job)
}

const OUTCOME_LABELS: Record<JobOutcome, string> = {
  ok: 'Finished',
  failed: 'Failed',
  cancelled: 'Cancelled',
  interrupted: 'Interrupted: Baihe restarted',
  partial: 'Finished with problems',
  kept_existing: 'Nothing new; existing lines kept',
}

const bare = (text: string) => text.trim().replace(/[.\s]+$/, '').toLowerCase()

// Plain-words outcome line for a finished job, or null when there is none.
// A message that only repeats the label ("Finished" / "Finished.") shows once.
export function jobOutcomeText(job: Pick<JobRecord, 'outcome' | 'outcome_message'>): string | null {
  if (!job.outcome) return null
  const label = OUTCOME_LABELS[job.outcome] ?? job.outcome
  const message = job.outcome_message?.trim()
  if (!message) return label
  // The stored reason already opens with this label ("Interrupted: Baihe restarted while…").
  if (job.outcome === 'interrupted' && bare(message).startsWith(bare(label))) return message
  return bare(message) === bare(label) ? message : `${label}: ${message}`
}

// The line under a status pill. The pill already says Failed or Cancelled,
// so those show the reason alone; a plain "Finished" adds nothing.
export function jobOutcomeDetail(job: Pick<JobRecord, 'status' | 'outcome' | 'outcome_message'>): string | null {
  if (!job.outcome) return null
  const message = job.outcome_message?.trim() || null
  if (job.outcome === 'failed' || job.outcome === 'cancelled') return message && bare(message) !== bare(OUTCOME_LABELS[job.outcome]) ? message : null
  // The pill says Cancelled (the stored status), so the line says why.
  if (job.outcome === 'interrupted') return message ?? OUTCOME_LABELS.interrupted
  if (job.outcome === 'ok') return message && bare(message) !== bare(OUTCOME_LABELS.ok) ? message : null
  return jobOutcomeText(job)
}

export interface JobListResponse {
  items: JobRecord[]
  count: number
}

export interface JobForceStopResult {
  job_id: string
  force_stopped: boolean
  status: string
  worker_still_running: boolean
}

export const FORCE_STOP_HINT =
  'Cancel was sent but the job has not stopped. Force stop ends it here; its worker may keep running in the background until it finishes.'

// Force stop shares Cancel's permission, so it follows the same rule.
export function offersForceStop(job: Pick<JobRecord, 'status' | 'can_force_stop' | 'owned_by_me'>, remoteAdmin: boolean): boolean {
  return job.status === 'running' && job.can_force_stop === true && offersCancel(job, remoteAdmin)
}

export interface JobCancelResult {
  job_id: string
  cancel_requested: boolean
  status: string
}

// Mirrors api/job_stage_schemas.py JobStageTimings (GET /api/jobs/{id}/stages).
export interface JobStage {
  stage: string
  started_at: number
  duration_seconds: number
  cost_usd: number
}

export interface JobStageRun {
  run_started_at: number
  running: boolean
  total_seconds: number
  cost_usd: number
  stages: JobStage[]
}

// Newest run first, at most 10; empty for a job recorded before stage timing.
export interface JobStageTimings {
  job_id: string
  runs: JobStageRun[]
}
