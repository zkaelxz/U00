import type { JobCancelResult, JobListResponse, JobRecord, JobStageTimings } from '../types/jobs'
import { getJson, postJson } from './client'

type Fetch = typeof fetch

export const getJob = (id: string, f?: Fetch) =>
  getJson<JobRecord>(`/api/jobs/${encodeURIComponent(id)}`, f)
export const listJobs = (f?: Fetch) => getJson<JobListResponse>('/api/jobs', f)
export const cancelJob = (id: string, f?: Fetch) =>
  postJson<JobCancelResult>(`/api/jobs/${encodeURIComponent(id)}/cancel`, undefined, f)
// Per-stage timing and estimated spend of the job's latest runs (404 if unseen).
export const getJobStages = (id: string, f?: Fetch) =>
  getJson<JobStageTimings>(`/api/jobs/${encodeURIComponent(id)}/stages`, f)
