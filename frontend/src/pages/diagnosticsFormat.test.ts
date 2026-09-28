import { describe, expect, it } from 'vitest'

import type { JobRecord } from '../types/jobs'
import { describeGpu, formatDuration, hasActiveJobs, splitDependencies } from './diagnosticsFormat'

const job = (o: Partial<JobRecord>): JobRecord => ({
  job_id: 'j', status: 'done', progress: null, message: '', error: null, description: null,
  gpu_touching: false, started_at: 100, finished_at: null, updated_at: 0, ...o,
})

const gpu = { name: null, vram_used_gb: null, vram_total_gb: null, torch_cuda_version: null, message: null }

describe('diagnosticsFormat', () => {
  it('splits and sorts dependencies', () => {
    const r = splitDependencies({
      b: { installed: false, powers: 'x', tier: 't' },
      a: { installed: true, powers: 'y', tier: 't' },
    })
    expect(r.installed.map((d) => d.name)).toEqual(['a'])
    expect(r.missing.map((d) => d.name)).toEqual(['b'])
  })
  it('detects active jobs', () => {
    expect(hasActiveJobs([job({ status: 'done' })])).toBe(false)
    expect(hasActiveJobs([job({ status: 'queued' })])).toBe(true)
  })
  it('formats durations', () => {
    expect(formatDuration(job({ started_at: null }), 0)).toBe('not started')
    expect(formatDuration(job({ finished_at: 142 }), 999)).toBe('42s')
    expect(formatDuration(job({}), 100 + 185)).toBe('3m 05s')
    expect(formatDuration(job({}), 100 + 3720)).toBe('1h 02m')
  })
  it('describes the GPU', () => {
    expect(describeGpu({ ...gpu, available: false })).toBe('No GPU detected.')
    expect(describeGpu({ ...gpu, available: true, name: 'X', vram_used_gb: 1, vram_total_gb: 8 })).toBe('X (1.0 / 8.0 GB in use)')
  })
})
