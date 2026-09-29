import { describe, expect, it } from 'vitest'

import type { JobRecord } from '../types/jobs'
import {
  describeGpu, formatDuration, formatSeconds, hasActiveJobs, jobDetail, jobStatusLine, splitDependencies,
} from './diagnosticsFormat'

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
  it('formats plain seconds and a job card line', () => {
    expect(formatSeconds(-3)).toBe('0s')
    expect(formatSeconds(65.7)).toBe('1m 05s')
    expect(jobStatusLine(job({ status: 'running', progress: 0.4 }), 100 + 185)).toBe('Running 40% · 3m 05s')
    expect(jobStatusLine(job({ status: 'error', progress: 0.4, finished_at: 110 }), 999)).toBe('Failed · 10s')
  })
  it('shows progress text only while a job runs, and the error when it failed', () => {
    expect(jobDetail(job({ status: 'running', message: 'Transcribing... 40%' }))).toBe('Transcribing... 40%')
    expect(jobDetail(job({ status: 'queued', message: 'Waiting' }))).toBe('Waiting')
    expect(jobDetail(job({ status: 'running', message: '' }))).toBeNull()
    expect(jobDetail(job({ status: 'done', message: 'Transcribing... 99%' }))).toBeNull()
    expect(jobDetail(job({ status: 'cancelled', message: 'Batch 2 of 5' }))).toBeNull()
    expect(jobDetail(job({ status: 'error', message: 'Batch 2 of 5', error: 'Timed out' }))).toBe('Timed out')
    expect(jobDetail(job({ status: 'error', message: 'Batch 2 of 5', error: null }))).toBe('Batch 2 of 5')
  })
  it('describes the GPU', () => {
    expect(describeGpu({ ...gpu, available: false })).toBe('No GPU detected.')
    expect(describeGpu({ ...gpu, available: true, name: 'X', vram_used_gb: 1, vram_total_gb: 8 })).toBe('X (1.0 / 8.0 GB in use)')
  })
})
