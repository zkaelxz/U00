import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// The suite has no DOM, so React's hooks are replaced by a minimal runner that keeps refs
// between renders and re-runs an effect when its dependencies change.
type Effect = { fn: () => void | (() => void); deps?: unknown[]; cleanup?: void | (() => void) }
const slots: { refs: { current: unknown }[]; effects: Effect[] } = { refs: [], effects: [] }
const state = { refIdx: 0, effIdx: 0, pending: [] as (() => void)[] }

vi.mock('react', () => ({
  useRef: (init: unknown) => {
    slots.refs[state.refIdx] ??= { current: init }
    return slots.refs[state.refIdx++]
  },
  useEffect: (fn: Effect['fn'], deps?: unknown[]) => {
    const prev = slots.effects[state.effIdx]
    const changed = !prev || !deps || !prev.deps || deps.some((d, i) => d !== prev.deps![i])
    if (changed) {
      const next: Effect = { fn, deps }
      if (prev?.cleanup) prev.cleanup()
      slots.effects[state.effIdx] = next
      state.pending.push(() => { next.cleanup = fn() })
    }
    state.effIdx++
  },
}))

import { useDelayedSave } from './useDelayedSave'

const save = vi.fn()
const render = <T,>(value: T | null) => {
  state.refIdx = 0
  state.effIdx = 0
  useDelayedSave(value, 1000, save)
  state.pending.splice(0).forEach((f) => f())
}
const unmount = () => slots.effects.forEach((e) => e.cleanup?.())

beforeEach(() => {
  vi.useFakeTimers()
  slots.refs = []
  slots.effects = []
  save.mockClear()
})
afterEach(() => vi.useRealTimers())

describe('useDelayedSave', () => {
  it('saves after the delay', () => {
    const v = { a: 1 }
    render(v)
    vi.advanceTimersByTime(1000)
    expect(save).toHaveBeenCalledWith(v, false)
  })
  it('saves a waiting value on leaving', () => {
    const v = { a: 1 }
    render(v)
    unmount()
    expect(save).toHaveBeenCalledWith(v, true)
  })
  it('does not save a value that was withdrawn before leaving', () => {
    render({ a: 1 })
    render(null)
    unmount()
    vi.advanceTimersByTime(5000)
    expect(save).not.toHaveBeenCalled()
  })
})
