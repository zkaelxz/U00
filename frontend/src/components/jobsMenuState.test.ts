import { describe, expect, it } from 'vitest'

import type { JobRecord } from '../types/jobs'
import { activeCount, badgeText, jobsButtonLabel, menuJobs } from './jobsMenuState'

const job = (id: string, status: string): JobRecord => ({
  job_id: id, status, progress: null, message: '', error: null, description: id,
  gpu_touching: false, started_at: 1, finished_at: null, updated_at: 1,
})

describe('jobsMenuState', () => {
  it('counts queued and running only', () => {
    expect(activeCount([job('a', 'running'), job('b', 'queued'), job('c', 'done')])).toBe(2)
    expect(activeCount([])).toBe(0)
  })
  it('lists active jobs first, then at most 5 finished', () => {
    const jobs = [
      ...['f1', 'f2', 'f3', 'f4', 'f5', 'f6', 'f7'].map((id) => job(id, 'done')),
      job('r', 'running'),
      job('q', 'queued'),
    ]
    expect(menuJobs(jobs).map((j) => j.job_id)).toEqual(['r', 'q', 'f1', 'f2', 'f3', 'f4', 'f5'])
  })
  it('never cuts active jobs', () => {
    const jobs = Array.from({ length: 8 }, (_, i) => job(`r${i}`, 'running'))
    expect(menuJobs(jobs)).toHaveLength(8)
  })
  it('labels the button', () => {
    expect(jobsButtonLabel(0)).toBe('Jobs')
    expect(jobsButtonLabel(1)).toBe('Jobs (1 job running)')
    expect(jobsButtonLabel(3)).toBe('Jobs (3 jobs running)')
    expect(badgeText(120)).toBe('99+')
  })
})
