// Test-only: a complete JobRecord, so specs name only the fields they care about.
import type { JobRecord } from './jobs'

export const testJob = (over: Partial<JobRecord> = {}): JobRecord => ({
  job_id: 'job', status: 'queued', progress: null, message: '', error: null, description: null, gpu_touching: false,
  started_at: null, finished_at: null, updated_at: 0, result: null, outcome: null, outcome_message: null, stale: false,
  stalled: false, can_force_stop: false, owned_by_me: false, drama_id: null, kind: 'other', page: null, ...over,
})
