import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/client'
import type { JobRecord } from '../types/jobs'
import { startJobPolling } from './useJob'

const job = (status: string): JobRecord => ({
  job_id: 'j', status, progress: null, message: '', error: null, description: null,
  gpu_touching: false, started_at: null, finished_at: null, updated_at: 0,
})

beforeEach(() => vi.useFakeTimers())
afterEach(() => vi.useRealTimers())

describe('startJobPolling', () => {
  it('polls until a terminal status, then calls onDone once and stops', async () => {
    const seq = ['queued', 'running', 'done']
    const fetchJob = vi.fn(async () => job(seq.shift() ?? 'done'))
    const updates: string[] = []
    const onDone = vi.fn()
    startJobPolling('j', { intervalMs: 100, fetchJob, onUpdate: (j) => updates.push(j.status), onError: vi.fn(), onDone })
    await vi.advanceTimersByTimeAsync(1000)
    expect(updates).toEqual(['queued', 'running', 'done'])
    expect(fetchJob).toHaveBeenCalledTimes(3)
    expect(onDone).toHaveBeenCalledTimes(1)
  })

  it.each(['error', 'cancelled'])('treats %s as terminal', async (s) => {
    const fetchJob = vi.fn(async () => job(s))
    const onDone = vi.fn()
    startJobPolling('j', { intervalMs: 50, fetchJob, onUpdate: vi.fn(), onError: vi.fn(), onDone })
    await vi.advanceTimersByTimeAsync(500)
    expect(fetchJob).toHaveBeenCalledTimes(1)
    expect(onDone).toHaveBeenCalledTimes(1)
  })

  it('stop() cancels the pending poll (unmount / id change)', async () => {
    const fetchJob = vi.fn(async () => job('running'))
    const stop = startJobPolling('j', { intervalMs: 100, fetchJob, onUpdate: vi.fn(), onError: vi.fn() })
    await vi.advanceTimersByTimeAsync(250)
    const n = fetchJob.mock.calls.length
    stop()
    await vi.advanceTimersByTimeAsync(1000)
    expect(fetchJob).toHaveBeenCalledTimes(n)
  })

  it('stop() during an in-flight request drops its result', async () => {
    let resolve!: (j: JobRecord) => void
    const fetchJob = vi.fn(() => new Promise<JobRecord>((r) => (resolve = r)))
    const onUpdate = vi.fn()
    const stop = startJobPolling('j', { fetchJob, onUpdate, onError: vi.fn() })
    stop()
    resolve(job('done'))
    await vi.advanceTimersByTimeAsync(10)
    expect(onUpdate).not.toHaveBeenCalled()
  })

  it('reports an ApiError and stops polling', async () => {
    const fetchJob = vi.fn(async () => { throw new ApiError(404, { code: 'not_found', message: 'nope' }) })
    const onError = vi.fn()
    startJobPolling('j', { intervalMs: 50, fetchJob, onUpdate: vi.fn(), onError })
    await vi.advanceTimersByTimeAsync(500)
    expect(onError).toHaveBeenCalledTimes(1)
    expect(onError.mock.calls[0][0].code).toBe('not_found')
    expect(fetchJob).toHaveBeenCalledTimes(1)
  })
})
