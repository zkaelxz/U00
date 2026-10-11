import { describe, expect, it } from 'vitest'

import type { JobRecord } from '../types/jobs'
import { createJobsSync } from './useJobs'

const job = (job_id: string, status: string): JobRecord => ({ job_id, status }) as unknown as JobRecord

describe('createJobsSync (GET /api/jobs against pushes)', () => {
  it('a GET whose snapshot predates a "done" push does not put the job back to running', () => {
    const sync = createJobsSync()
    const token = sync.begin()
    // The push lands first, the GET (read on the server before the job finished) after.
    sync.push('a', job('a', 'done'))
    expect(sync.settle(token, [job('a', 'running'), job('b', 'running')])).toEqual([job('a', 'done'), job('b', 'running')])
  })

  it('a job_gone pushed during the GET removes the job from its answer', () => {
    const sync = createJobsSync()
    const token = sync.begin()
    sync.push('a', null)
    expect(sync.settle(token, [job('a', 'running'), job('b', 'done')])).toEqual([job('b', 'done')])
  })

  it('an older GET answering after a newer one started is dropped', () => {
    const sync = createJobsSync()
    const first = sync.begin()
    const second = sync.begin()
    expect(sync.settle(first, [job('a', 'running')])).toBeNull()
    expect(sync.isCurrent(first)).toBe(false)
    expect(sync.settle(second, [job('a', 'done')])).toEqual([job('a', 'done')])
  })

  it('pushes before a GET started are not replayed over it: its snapshot already has them', () => {
    const sync = createJobsSync()
    sync.begin()
    sync.push('a', job('a', 'running'))
    const token = sync.begin()
    expect(sync.settle(token, [job('a', 'done')])).toEqual([job('a', 'done')])
  })
})
