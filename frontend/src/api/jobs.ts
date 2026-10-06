import type { JobCancelResult, JobListResponse, JobRecord, JobStageTimings } from '../types/jobs'
import { getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

export const getJob = (id: string, f?: Fetch) =>
  getJson<JobRecord>(`/api/jobs/${encodeURIComponent(id)}`, f)
export const listJobs = (f?: Fetch) => getJson<JobListResponse>('/api/jobs', f)
export const cancelJob = (id: string, f?: Fetch) =>
  postJson<JobCancelResult>(`/api/jobs/${encodeURIComponent(id)}/cancel`, undefined, f)
// Per-stage timing and estimated spend of the job's latest runs (404 if unseen).
export const getJobStages = (id: string, f?: Fetch) =>
  getJson<JobStageTimings>(`/api/jobs/${encodeURIComponent(id)}/stages`, f)
// PC only. Permanently erases a finished job's record; 409 while it is queued or running.
export const deleteJob = (id: string, f?: Fetch) =>
  postJson<{ job_id: string; deleted: boolean }>(`/api/jobs/${encodeURIComponent(id)}/delete`, { confirm: true }, pcOnlyFetch(f))
// PC only. Permanently erases every finished job's record.
export const clearFinishedJobs = (f?: Fetch) =>
  postJson<{ deleted_count: number }>('/api/jobs/clear-finished', { confirm: true }, pcOnlyFetch(f))
