import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { watchApiReachability } from './App'

// Runs the poll against a tiny event target so it needs no DOM.
function makeTarget(visibility: 'visible' | 'hidden' = 'visible') {
  const handlers = new Map<string, Array<() => void>>()
  const target = {
    visibilityState: visibility,
    addEventListener: (e: string, h: () => void) => handlers.set(e, [...(handlers.get(e) ?? []), h]),
    removeEventListener: (e: string, h: () => void) => handlers.set(e, (handlers.get(e) ?? []).filter((x) => x !== h)),
    fire: (e: string) => (handlers.get(e) ?? []).forEach((h) => h()),
  }
  return target
}

const flush = () => vi.advanceTimersByTimeAsync(0)

describe('watchApiReachability', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('does not report down for one failed check that the next one clears', async () => {
    const probe = vi.fn().mockRejectedValueOnce(new Error('suspended')).mockResolvedValue({})
    const states: string[] = []
    const t = makeTarget()
    watchApiReachability(probe, (s) => states.push(s), t as never, t as never)
    await flush()
    await vi.advanceTimersByTimeAsync(3_000)
    expect(probe).toHaveBeenCalledTimes(2)
    expect(states).toEqual(['up'])
  })

  it('reports down once the confirming check fails, and up again on return to the app', async () => {
    const probe = vi.fn().mockRejectedValue(new Error('down'))
    const states: string[] = []
    const t = makeTarget()
    watchApiReachability(probe, (s) => states.push(s), t as never, t as never)
    await flush()
    await vi.advanceTimersByTimeAsync(3_000)
    expect(states).toEqual(['down'])
    probe.mockResolvedValue({})
    t.fire('visibilitychange')
    await flush()
    expect(states).toEqual(['down', 'up'])
  })

  it('does not check while the page is hidden', async () => {
    const probe = vi.fn().mockRejectedValue(new Error('down'))
    const states: string[] = []
    const t = makeTarget('hidden')
    watchApiReachability(probe, (s) => states.push(s), t as never, t as never)
    await vi.advanceTimersByTimeAsync(60_000)
    expect(probe).not.toHaveBeenCalled()
    expect(states).toEqual([])
  })
})
