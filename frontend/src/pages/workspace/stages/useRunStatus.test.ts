import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../../../api/client'
import { MAX_BACKOFF_MS, SHOWN_AFTER_FAILURES, startRunPolling, type VisibilitySource } from './useRunStatus'

class FakeDoc implements VisibilitySource {
  hidden = false
  private fns = new Set<() => void>()
  addEventListener(_t: 'visibilitychange', fn: () => void) {
    this.fns.add(fn)
  }
  removeEventListener(_t: 'visibilitychange', fn: () => void) {
    this.fns.delete(fn)
  }
  setHidden(h: boolean) {
    this.hidden = h
    this.fns.forEach((f) => f())
  }
}

const running = { status: 'running' }
const net = () => new ApiError(0, { code: 'network_error', message: 'down' })
const flush = () => vi.advanceTimersByTimeAsync(0)

beforeEach(() => vi.useFakeTimers())
afterEach(() => vi.useRealTimers())

function setup(load: () => Promise<{ status: string }>, doc = new FakeDoc()) {
  const onStatus = vi.fn()
  const onError = vi.fn()
  const stop = startRunPolling({ load, intervalMs: 1000, onStatus, onError, doc })
  return { onStatus, onError, stop, doc }
}

describe('startRunPolling', () => {
  it('keeps polling while active and stops once the run is over', async () => {
    const load = vi.fn().mockResolvedValueOnce(running).mockResolvedValueOnce({ status: 'done' })
    const { onStatus } = setup(load)
    await flush()
    await vi.advanceTimersByTimeAsync(1000)
    await vi.advanceTimersByTimeAsync(60_000)
    expect(load).toHaveBeenCalledTimes(2)
    expect(onStatus).toHaveBeenCalledTimes(2)
  })

  it('backs off with a cap and keeps retrying after the error is shown', async () => {
    const load = vi.fn().mockRejectedValue(net())
    const { onError } = setup(load)
    await vi.advanceTimersByTimeAsync(0)
    expect(onError).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(10 * MAX_BACKOFF_MS)
    expect(onError).toHaveBeenCalled()
    const callsAfterShown = load.mock.calls.length
    expect(callsAfterShown).toBeGreaterThan(SHOWN_AFTER_FAILURES)
    await vi.advanceTimersByTimeAsync(MAX_BACKOFF_MS)
    expect(load.mock.calls.length).toBe(callsAfterShown + 1)
  })

  it('shows a 4xx at once but still retries, and recovers on success', async () => {
    const load = vi
      .fn()
      .mockRejectedValueOnce(new ApiError(404, { code: 'not_found', message: 'gone' }))
      .mockResolvedValue({ status: 'done' })
    const { onError, onStatus } = setup(load)
    await flush()
    expect(onError).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(2000)
    expect(onStatus).toHaveBeenCalledWith({ status: 'done' })
  })

  it('pauses while the tab is hidden and resumes at once when it is visible', async () => {
    const doc = new FakeDoc()
    const load = vi.fn().mockResolvedValue(running)
    setup(load, doc)
    await flush()
    doc.hidden = true
    await vi.advanceTimersByTimeAsync(10_000)
    expect(load).toHaveBeenCalledTimes(1)
    doc.setHidden(false)
    await flush()
    expect(load).toHaveBeenCalledTimes(2)
  })

  it('does not poll after stop', async () => {
    const load = vi.fn().mockResolvedValue(running)
    const { stop } = setup(load)
    await flush()
    stop()
    await vi.advanceTimersByTimeAsync(10_000)
    expect(load).toHaveBeenCalledTimes(1)
  })
})
