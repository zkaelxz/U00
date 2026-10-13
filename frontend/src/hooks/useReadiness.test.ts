import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { SetupReadiness } from '../types/setup'

const read = vi.fn()
vi.mock('../api/setup', () => ({ getReadiness: () => read() }))

const READY = { faster_whisper: true } as SetupReadiness

async function fresh() {
  vi.resetModules()
  return import('./useReadiness')
}

describe('the shared readiness read', () => {
  beforeEach(() => {
    read.mockReset()
    vi.useRealTimers()
  })

  it('reads once for callers that ask together', async () => {
    read.mockResolvedValue(READY)
    const { loadReadiness } = await fresh()
    await Promise.all([loadReadiness(), loadReadiness(), loadReadiness()])
    expect(read).toHaveBeenCalledTimes(1)
  })

  it('keeps an answer for a few seconds, then reads again', async () => {
    vi.useFakeTimers()
    read.mockResolvedValue(READY)
    const { loadReadiness } = await fresh()
    await loadReadiness()
    await loadReadiness()
    expect(read).toHaveBeenCalledTimes(1)
    vi.advanceTimersByTime(5000)
    await loadReadiness()
    expect(read).toHaveBeenCalledTimes(2)
  })

  it('refresh reads again at once, for after an install', async () => {
    read.mockResolvedValue(READY)
    const { loadReadiness, refreshReadiness } = await fresh()
    await loadReadiness()
    await refreshReadiness()
    expect(read).toHaveBeenCalledTimes(2)
  })

  it('a failed read leaves the last good answer instead of guessing', async () => {
    const { loadReadiness, refreshReadiness, resetReadiness } = await fresh()
    read.mockResolvedValueOnce(READY)
    await loadReadiness()
    read.mockRejectedValueOnce(new Error('offline'))
    await expect(refreshReadiness()).resolves.toBeUndefined()
    // The next call is not blocked by the failed one.
    read.mockResolvedValueOnce({ faster_whisper: false } as SetupReadiness)
    await refreshReadiness()
    expect(read).toHaveBeenCalledTimes(3)
    resetReadiness()
  })
})
