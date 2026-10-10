import { describe, expect, it } from 'vitest'

import type { JobRecord } from '../../../types/jobs'
import { lastRunCounts, lastRunFor } from './lastRun'
import { translateJobIds } from '../stageJobIds'

const rec = (over: Partial<JobRecord>): JobRecord => ({
  job_id: 'translate_3', status: 'done', progress: 1, message: 'Done', error: null, description: null,
  gpu_touching: false, started_at: 10, finished_at: 20, updated_at: 20, drama_id: 3, kind: 'translate', ...over,
})

describe('lastRunFor', () => {
  it('picks the newest finished job among the stage ids and ignores other dramas and live jobs', () => {
    const jobs = [
      rec({ job_id: 'translate_3', finished_at: 100 }),
      rec({ job_id: 'translate_3', status: 'error', finished_at: 300, error: 'boom' }),
      rec({ job_id: 'translate_4', drama_id: 4, finished_at: 900 }),
      rec({ job_id: 'dub_3', kind: 'dub', finished_at: 950 }),
      rec({ job_id: 'bulk_translate_3', status: 'running', finished_at: null, updated_at: 999 }),
    ]
    expect(lastRunFor(jobs, 3, translateJobIds(3))?.status).toBe('error')
    expect(lastRunFor(jobs, 4, translateJobIds(4))?.job_id).toBe('translate_4')
    expect(lastRunFor(jobs, 5, translateJobIds(5))).toBeNull()
  })

  it('refuses a record whose drama_id names another title even when the id matches', () => {
    expect(lastRunFor([rec({ drama_id: 4 })], 3, translateJobIds(3))).toBeNull()
  })

  it('accepts a record from an older server without drama_id, orders by updated_at when unfinished', () => {
    const jobs = [
      rec({ drama_id: undefined, finished_at: null, updated_at: 5, status: 'cancelled' }),
      rec({ drama_id: undefined, finished_at: null, updated_at: 7 }),
    ]
    expect(lastRunFor(jobs, 3, translateJobIds(3))?.status).toBe('done')
    expect(lastRunFor(null, 3, translateJobIds(3))).toBeNull()
  })
})

describe('lastRunCounts', () => {
  it('reads the counts the record carries, in order, and skips zero failures', () => {
    expect(lastRunCounts(rec({ result: { line_count: 120, errors: ['x', 'y'], fixed_count: 0 } }))).toBe('120 lines · 2 failed')
    expect(lastRunCounts(rec({ result: { line_count: 1, failed_count: 0 } }))).toBe('1 line')
    expect(lastRunCounts(rec({ result: { line_count: 0 } }))).toBe('0 lines')
    expect(lastRunCounts(rec({ result: null }))).toBe('')
    expect(lastRunCounts(rec({ result: { device: 'cuda', note: 'text' } }))).toBe('')
  })

  it('counts dramas for a bulk library run', () => {
    expect(lastRunCounts(rec({ result: { bulk: { translated_count: 3, failed_count: 1 }, line_count: 9 } }))).toBe('3 dramas · 1 failed')
  })
})
