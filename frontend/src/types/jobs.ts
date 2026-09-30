// Mirrors api/schemas.py JobRecord / JobListResponse / JobCancelResult.

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
}

export type JobOutcome = 'ok' | 'failed' | 'cancelled' | 'partial' | 'kept_existing'

// A terminal job that did not fully succeed, even when status is 'done'
// (a transcription that returned early with a failure reason, say).
export function jobFailed(job: Pick<JobRecord, 'status' | 'outcome'>): boolean {
  return job.status === 'error' || job.outcome === 'failed' || job.outcome === 'cancelled'
}

// Done and not failed/cancelled: safe to treat the job's output as ready.
export function jobSucceeded(job: Pick<JobRecord, 'status' | 'outcome'> | null | undefined): boolean {
  return !!job && job.status === 'done' && !jobFailed(job)
}

const OUTCOME_LABELS: Record<JobOutcome, string> = {
  ok: 'Finished',
  failed: 'Failed',
  cancelled: 'Cancelled',
  partial: 'Finished with problems',
  kept_existing: 'Nothing new; existing lines kept',
}

// Plain-words outcome line for a finished job, or null when there is none.
export function jobOutcomeText(job: Pick<JobRecord, 'outcome' | 'outcome_message'>): string | null {
  if (!job.outcome) return null
  const label = OUTCOME_LABELS[job.outcome] ?? job.outcome
  return job.outcome_message ? `${label}: ${job.outcome_message}` : label
}

export interface JobListResponse {
  items: JobRecord[]
  count: number
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
