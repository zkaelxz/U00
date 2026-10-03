import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../../api/client'
import { isSameJobConflict } from './sourcesFormat'
import { LOST_CONTACT, isStartedHere, pollSourcesJob } from './useSourcesJob'

const running = { job_id: 'j', status: 'running', progress: 0.1, message: null, result: null }
const done = { job_id: 'j', status: 'done', progress: 1, message: null, result: { kind: 'search' } }

function handlers() {
  return { onUpdate: vi.fn(), onIdle: vi.fn(), onError: vi.fn() }
}

beforeEach(() => vi.useFakeTimers())
afterEach(() => vi.useRealTimers())

describe('pollSourcesJob', () => {
  it('polls every 1.5 s until done', async () => {
    const fetchResult = vi.fn().mockResolvedValueOnce(running).mockResolvedValueOnce(done)
    const h = handlers()
    pollSourcesJob('j', { ...h, fetchResult })
    await vi.advanceTimersByTimeAsync(0)
    expect(h.onUpdate).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1500)
    expect(h.onUpdate).toHaveBeenLastCalledWith(done)
    await vi.advanceTimersByTimeAsync(5000)
    expect(fetchResult).toHaveBeenCalledTimes(2)
  })

  it('a first-look 404 is idle, not an error', async () => {
    const fetchResult = vi.fn().mockRejectedValue(new ApiError(404, { code: 'not_found', message: 'x' }))
    const h = handlers()
    pollSourcesJob('j', { ...h, fetchResult })
    await vi.advanceTimersByTimeAsync(0)
    expect(h.onIdle).toHaveBeenCalled()
    expect(h.onError).not.toHaveBeenCalled()
  })

  it('a first-look "idle" answer is idle, not an error, and ends polling', async () => {
    const fetchResult = vi.fn().mockResolvedValue({ job_id: '', status: 'idle', progress: 0, message: '', result: null })
    const h = handlers()
    pollSourcesJob('j', { ...h, fetchResult })
    await vi.advanceTimersByTimeAsync(0)
    expect(h.onIdle).toHaveBeenCalled()
    expect(h.onUpdate).not.toHaveBeenCalled()
    expect(h.onError).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(5000)
    expect(fetchResult).toHaveBeenCalledTimes(1)
  })

  it('"idle" after a run was seen is the vanished-job error', async () => {
    const idle = { job_id: '', status: 'idle', progress: 0, message: '', result: null }
    const fetchResult = vi.fn().mockResolvedValueOnce(running).mockResolvedValueOnce(idle)
    const h = handlers()
    pollSourcesJob('j', { ...h, fetchResult })
    await vi.advanceTimersByTimeAsync(1500)
    expect(h.onIdle).not.toHaveBeenCalled()
    expect(h.onError).toHaveBeenCalledTimes(1)
    expect(h.onError.mock.calls[0][0]).toMatchObject({ status: 404 })
  })

  it.each([400, 409, 503])('a %i ends polling with the error', async (status) => {
    const err = new ApiError(status, { code: 'x', message: 'x' })
    const fetchResult = vi.fn().mockResolvedValueOnce(running).mockRejectedValueOnce(err)
    const h = handlers()
    pollSourcesJob('j', { ...h, fetchResult })
    await vi.advanceTimersByTimeAsync(1500)
    expect(h.onError).toHaveBeenCalledWith(err)
    await vi.advanceTimersByTimeAsync(10_000)
    expect(fetchResult).toHaveBeenCalledTimes(2)
  })

  it('retries a lost connection 3 times, then says so', async () => {
    const fetchResult = vi.fn().mockRejectedValue(new ApiError(0, { code: 'network_error', message: 'x' }))
    const h = handlers()
    pollSourcesJob('j', { ...h, fetchResult })
    await vi.advanceTimersByTimeAsync(60_000)
    expect(fetchResult).toHaveBeenCalledTimes(4)
    expect(h.onError).toHaveBeenCalledTimes(1)
    expect(h.onError.mock.calls[0][0].message).toBe(LOST_CONTACT)
  })

  it('a 500 is retried, then its own error is shown', async () => {
    const err = new ApiError(500, { code: 'application_error', message: 'Parse failed.' })
    const fetchResult = vi.fn().mockResolvedValueOnce(running).mockRejectedValue(err)
    const h = handlers()
    pollSourcesJob('j', { ...h, fetchResult })
    await vi.advanceTimersByTimeAsync(60_000)
    expect(fetchResult).toHaveBeenCalledTimes(5)
    expect(h.onError).toHaveBeenCalledWith(err)
  })

  it('stops when asked', async () => {
    const fetchResult = vi.fn().mockResolvedValue(running)
    const h = handlers()
    const stop = pollSourcesJob('j', { ...h, fetchResult })
    await vi.advanceTimersByTimeAsync(0)
    stop()
    await vi.advanceTimersByTimeAsync(10_000)
    expect(fetchResult).toHaveBeenCalledTimes(1)
  })
})

describe('isSameJobConflict', () => {
  it('reattaches only to the same running job', () => {
    const conflict = (job_id: string) => new ApiError(409, { code: 'conflict', message: 'x', details: { job_id } })
    expect(isSameJobConflict(conflict('sources_search'), 'sources_search')).toBe(true)
    expect(isSameJobConflict(conflict('other'), 'sources_search')).toBe(false)
    expect(isSameJobConflict(new ApiError(400, { code: 'x', message: 'x' }), 'sources_search')).toBe(false)
  })
})

describe('isStartedHere', () => {
  it('holds only for the id the run was started under', () => {
    expect(isStartedHere('sourceimport_21', 'sourceimport_21')).toBe(true)
    // Picked another drama after importing: its stored run was not started here.
    expect(isStartedHere('sourceimport_21', 'sourceimport_12')).toBe(false)
    expect(isStartedHere(null, 'sourceimport_12')).toBe(false)
    expect(isStartedHere(null, null)).toBe(false)
  })
})
