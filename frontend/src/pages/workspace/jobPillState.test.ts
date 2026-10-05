import { describe, expect, it } from 'vitest'

import type { JobRecord } from '../../types/jobs'
import { activeJobsFor, finishedFlash, jobPercent, jobVerb, pillText } from './jobPillState'

const job = (over: Partial<JobRecord>): JobRecord => ({
  job_id: 'j', status: 'running', progress: null, message: '', error: null, description: null,
  gpu_touching: false, started_at: 1, finished_at: null, updated_at: 1, drama_id: 3, kind: 'translate', ...over,
})

describe('jobVerb', () => {
  it('names each kind and falls back to Working', () => {
    expect(jobVerb('translate')).toBe('Translating')
    expect(jobVerb('transcribe')).toBe('Transcribing')
    expect(jobVerb('align')).toBe('Aligning')
    expect(jobVerb('dub')).toBe('Dubbing')
    expect(jobVerb('export')).toBe('Exporting')
    expect(jobVerb('review')).toBe('Reviewing')
    expect(jobVerb('import')).toBe('Importing')
    expect(jobVerb('other')).toBe('Working')
    expect(jobVerb(undefined)).toBe('Working')
    expect(jobVerb('something_new')).toBe('Working')
  })
})

describe('jobPercent', () => {
  it('rounds, clamps and treats null as unknown', () => {
    expect(jobPercent(0.424)).toBe(42)
    expect(jobPercent(1.4)).toBe(100)
    expect(jobPercent(-1)).toBe(0)
    expect(jobPercent(null)).toBeNull()
    expect(jobPercent(undefined)).toBeNull()
  })
})

describe('activeJobsFor', () => {
  it('keeps queued and running jobs of this title only', () => {
    const jobs = [
      job({ job_id: 'a' }),
      job({ job_id: 'b', status: 'queued' }),
      job({ job_id: 'c', status: 'done' }),
      job({ job_id: 'd', drama_id: 4 }),
      job({ job_id: 'e', drama_id: null }),
      job({ job_id: 'f', drama_id: undefined }),
    ]
    expect(activeJobsFor(jobs, 3).map((j) => j.job_id)).toEqual(['a', 'b'])
  })
})

describe('pillText', () => {
  it('shows verb and percent, or just the verb when progress is unknown', () => {
    expect(pillText([job({ progress: 0.42 })])).toBe('Translating 42%')
    expect(pillText([job({ progress: null, kind: 'transcribe' })])).toBe('Transcribing')
    expect(pillText([job({ kind: undefined, progress: 0.1 })])).toBe('Working 10%')
  })
  it('counts several jobs and is null for none', () => {
    expect(pillText([job({ job_id: 'a' }), job({ job_id: 'b' })])).toBe('2 jobs')
    expect(pillText([])).toBeNull()
  })
})

describe('finishedFlash', () => {
  const prev = new Set(['a'])
  it('is null while the job still runs or nothing was running', () => {
    expect(finishedFlash(prev, [job({ job_id: 'a' })], 3)).toBeNull()
    expect(finishedFlash(new Set(), [job({ job_id: 'a', status: 'done' })], 3)).toBeNull()
  })
  it('reports done, failed and cancelled outcomes', () => {
    expect(finishedFlash(prev, [job({ job_id: 'a', status: 'done', outcome: 'ok' })], 3)).toBe('done')
    expect(finishedFlash(prev, [job({ job_id: 'a', status: 'error' })], 3)).toBe('failed')
    expect(finishedFlash(prev, [job({ job_id: 'a', status: 'done', outcome: 'failed' })], 3)).toBe('failed')
    expect(finishedFlash(prev, [job({ job_id: 'a', status: 'cancelled' })], 3)).toBe('failed')
  })
  it('treats a job that left the list as done, and lets failure win', () => {
    expect(finishedFlash(prev, [], 3)).toBe('done')
    const two = new Set(['a', 'b'])
    expect(finishedFlash(two, [job({ job_id: 'a', status: 'done' }), job({ job_id: 'b', status: 'error' })], 3)).toBe('failed')
  })
})
