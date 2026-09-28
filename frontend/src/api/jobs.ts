import type { JobCancelResult, JobListResponse, JobRecord } from '../types/jobs'
import { getJson, postJson } from './client'

type Fetch = typeof fetch

export const getJob = (id: string, f?: Fetch) =>
  getJson<JobRecord>(`/api/jobs/${encodeURIComponent(id)}`, f)
export const listJobs = (f?: Fetch) => getJson<JobListResponse>('/api/jobs', f)
export const cancelJob = (id: string, f?: Fetch) =>
  postJson<JobCancelResult>(`/api/jobs/${encodeURIComponent(id)}/cancel`, undefined, f)
