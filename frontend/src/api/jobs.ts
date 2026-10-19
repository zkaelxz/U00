import type { JobCancelResult, JobForceStopResult, JobListResponse, JobQueueSummary, JobRecord, JobStageTimings } from '../types/jobs'
import { getJson, postJson, type Fetch } from './client'
import { pcOnlyFetch } from './pcOnly'

export const getJob = (id: string, f?: Fetch) =>
  getJson<JobRecord>(`/api/jobs/${encodeURIComponent(id)}`, f)
const probing = (f: Fetch = fetch): Fetch => (input, init) =>
  f(input, { ...init, headers: { ...(init?.headers as Record<string, string> | undefined), 'X-Baihe-Probe': '1' } })
// For a job that usually doesn't exist yet (a stage's fixed per-title id): null, not a 404 in the console.
export const probeJob = (id: string, f?: Fetch) =>
  getJson<JobRecord | null>(`/api/jobs/${encodeURIComponent(id)}`, probing(f))
export const listJobs = (f?: Fetch) => getJson<JobListResponse>('/api/jobs', f)
// Running/waiting counts for everyone, and the caller's own places in the GPU queue.
export const getQueueSummary = (f?: Fetch) => getJson<JobQueueSummary>('/api/jobs/queue-summary', f)
export const cancelJob = (id: string, f?: Fetch) =>
  postJson<JobCancelResult>(`/api/jobs/${encodeURIComponent(id)}/cancel`, undefined, f)
// Closes the record of a thread job that ignored Cancel for over a minute (409 otherwise).
export const forceStopJob = (id: string, f?: Fetch) =>
  postJson<JobForceStopResult>(`/api/jobs/${encodeURIComponent(id)}/force-stop`, undefined, f)
// Per-stage timing and estimated spend of the job's latest runs (404 if unseen).
// A 200 that is not JSON reads as null (e.g. the app page answering a route the server lacks);
// refuse it here so callers never see a body without `runs`.
export const getJobStages = async (id: string, f?: Fetch) => {
  const out = await getJson<JobStageTimings | null>(`/api/jobs/${encodeURIComponent(id)}/stages`, f)
  if (!out || !Array.isArray(out.runs)) throw new Error('The job stage timing response was not understood.')
  return out
}
// PC only. Permanently erases a finished job's record; 409 while it is queued or running.
export const deleteJob = (id: string, f?: Fetch) =>
  postJson<{ job_id: string; deleted: boolean }>(`/api/jobs/${encodeURIComponent(id)}/delete`, { confirm: true }, pcOnlyFetch(f))
// PC only. Permanently erases every finished job's record.
export const clearFinishedJobs = (f?: Fetch) =>
  postJson<{ deleted_count: number }>('/api/jobs/clear-finished', { confirm: true }, pcOnlyFetch(f))
