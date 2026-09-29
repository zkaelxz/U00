import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/client'
import type { JobRecord } from '../types/jobs'
import { pickRunState, startJobPolling } from './useJob'

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

  it('reports a 4xx ApiError immediately and stops polling', async () => {
    const fetchJob = vi.fn(async () => { throw new ApiError(404, { code: 'not_found', message: 'nope' }) })
    const onError = vi.fn()
    startJobPolling('j', { intervalMs: 50, fetchJob, onUpdate: vi.fn(), onError })
    await vi.advanceTimersByTimeAsync(500)
    expect(onError).toHaveBeenCalledTimes(1)
    expect(onError.mock.calls[0][0].code).toBe('not_found')
    expect(fetchJob).toHaveBeenCalledTimes(1)
  })

  it('keeps polling through transient failures and recovers', async () => {
    let n = 0
    const fetchJob = vi.fn(async () => {
      n += 1
      if (n === 1) throw new ApiError(0, { code: 'network_error', message: 'down' })
      if (n === 2) throw new ApiError(503, { code: 'internal_error', message: 'busy' })
      return job(n === 3 ? 'running' : 'done')
    })
    const onError = vi.fn()
    const onDone = vi.fn()
    startJobPolling('j', { intervalMs: 100, fetchJob, onUpdate: vi.fn(), onError, onDone })
    await vi.advanceTimersByTimeAsync(5000)
    expect(onError).not.toHaveBeenCalled()
    expect(onDone).toHaveBeenCalledTimes(1)
    expect(fetchJob).toHaveBeenCalledTimes(4)
  })

  it('surfaces the error after maxFailures consecutive transient failures', async () => {
    const fetchJob = vi.fn(async () => { throw new ApiError(500, { code: 'internal_error', message: 'x' }) })
    const onError = vi.fn()
    startJobPolling('j', { intervalMs: 10, maxFailures: 3, fetchJob, onUpdate: vi.fn(), onError })
    await vi.advanceTimersByTimeAsync(60000)
    expect(fetchJob).toHaveBeenCalledTimes(3)
    expect(onError).toHaveBeenCalledTimes(1)
  })

  it('treats a non-ApiError rejection as transient, then reports network_error', async () => {
    const fetchJob = vi.fn(async () => { throw new TypeError('boom') })
    const onError = vi.fn()
    startJobPolling('j', { intervalMs: 10, maxFailures: 2, fetchJob, onUpdate: vi.fn(), onError })
    await vi.advanceTimersByTimeAsync(60000)
    expect(fetchJob).toHaveBeenCalledTimes(2)
    expect(onError.mock.calls[0][0].code).toBe('network_error')
  })

  it('a success resets the failure count', async () => {
    const seq = ['fail', 'fail', 'ok', 'fail', 'fail', 'done']
    const fetchJob = vi.fn(async () => {
      const s = seq.shift()
      if (s === 'fail') throw new ApiError(502, { code: 'internal_error', message: 'x' })
      return job(s === 'ok' ? 'running' : 'done')
    })
    const onError = vi.fn()
    const onDone = vi.fn()
    startJobPolling('j', { intervalMs: 10, maxFailures: 3, fetchJob, onUpdate: vi.fn(), onError, onDone })
    await vi.advanceTimersByTimeAsync(60000)
    expect(onError).not.toHaveBeenCalled()
    expect(onDone).toHaveBeenCalledTimes(1)
  })

  it('caps the backoff delay at maxBackoffMs', async () => {
    const fetchJob = vi.fn(async () => { throw new ApiError(0, { code: 'network_error', message: 'x' }) })
    startJobPolling('j', { intervalMs: 100, maxBackoffMs: 300, maxFailures: 10, fetchJob, onUpdate: vi.fn(), onError: vi.fn() })
    await vi.advanceTimersByTimeAsync(0)
    expect(fetchJob).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(200) // first retry after 200ms
    expect(fetchJob).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(300) // capped, not 400
    expect(fetchJob).toHaveBeenCalledTimes(3)
    await vi.advanceTimersByTimeAsync(300)
    expect(fetchJob).toHaveBeenCalledTimes(4)
  })

  it('stop() cancels a pending backoff retry', async () => {
    const fetchJob = vi.fn(async () => { throw new ApiError(0, { code: 'network_error', message: 'x' }) })
    const stop = startJobPolling('j', { intervalMs: 100, fetchJob, onUpdate: vi.fn(), onError: vi.fn() })
    await vi.advanceTimersByTimeAsync(0)
    stop()
    await vi.advanceTimersByTimeAsync(10000)
    expect(fetchJob).toHaveBeenCalledTimes(1)
  })
})

describe('restarting a run with the same fixed job id', () => {
  it('a second polling loop on the same id polls again and reports the new run', async () => {
    const fetchJob = vi.fn().mockResolvedValueOnce(job('done')).mockResolvedValueOnce(job('running'))
    const seen: string[] = []
    const opts = { intervalMs: 100, fetchJob, onUpdate: (j: JobRecord) => seen.push(j.status), onError: vi.fn() }
    startJobPolling('transcribe_1', opts)
    await vi.advanceTimersByTimeAsync(0)
    startJobPolling('transcribe_1', opts) // second run, same id
    await vi.advanceTimersByTimeAsync(0)
    expect(seen).toEqual(['done', 'running'])
  })

  it('pickRunState drops the previous run\'s state when the run key changes', () => {
    const state = { id: 'transcribe_1', runKey: 1, job: job('done') }
    expect(pickRunState(state, 'transcribe_1', 1)).toBe(state)
    expect(pickRunState(state, 'transcribe_1', 2)).toBeNull()
    expect(pickRunState(state, 'transcribe_2', 1)).toBeNull()
    expect(pickRunState(null, 'transcribe_1', 1)).toBeNull()
  })
})
