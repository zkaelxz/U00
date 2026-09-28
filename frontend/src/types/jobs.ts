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
