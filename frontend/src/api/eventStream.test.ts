import { describe, expect, it, vi } from 'vitest'

import {
  BASE_BACKOFF_MS, FALLBACK_AFTER, MAX_BACKOFF_MS, SILENCE_MS, backoffMs, createEventHub,
  type EventSourceLike, type StreamState,
} from './eventStream'

class FakeSource implements EventSourceLike {
  onopen: ((ev: Event) => unknown) | null = null
  onerror: ((ev: Event) => unknown) | null = null
  closed = false
  handlers = new Map<string, (ev: MessageEvent) => void>()
  readonly url: string
  constructor(url: string) {
    this.url = url
  }
  addEventListener(type: string, fn: (ev: MessageEvent) => void) {
    this.handlers.set(type, fn)
  }
  close() {
    this.closed = true
  }
  open() {
    this.onopen?.(new Event('open'))
  }
  fail() {
    this.onerror?.(new Event('error'))
  }
  send(type: string, data: unknown) {
    this.handlers.get(type)?.({ data: typeof data === 'string' ? data : JSON.stringify(data) } as MessageEvent)
  }
}

function setup() {
  const sources: FakeSource[] = []
  const timers: { fn: () => void; ms: number }[] = []
  const hub = createEventHub({
    url: '/api/events',
    create: (url) => {
      const s = new FakeSource(url)
      sources.push(s)
      return s
    },
    setTimer: (fn, ms) => {
      const t = { fn, ms }
      timers.push(t)
      return t
    },
    clearTimer: (t) => {
      const i = timers.indexOf(t as (typeof timers)[number])
      if (i >= 0) timers.splice(i, 1)
    },
  })
  const runTimer = () => timers.shift()!.fn()
  return { hub, sources, timers, runTimer }
}

describe('createEventHub', () => {
  it('opens one shared stream for the first subscriber and closes it with the last', () => {
    const { hub, sources } = setup()
    const a = hub.subscribe({})
    const b = hub.subscribe({})
    expect(sources).toHaveLength(1)
    expect(sources[0].url).toBe('/api/events')
    a()
    expect(sources[0].closed).toBe(false)
    b()
    expect(sources[0].closed).toBe(true)
    expect(hub.state().mode).toBe('connecting')
  })

  it('goes connecting -> push and bumps syncs on every open', () => {
    const { hub, sources, runTimer } = setup()
    const states: StreamState[] = []
    hub.subscribe({ onState: (s) => states.push(s) })
    expect(hub.state()).toEqual({ mode: 'connecting', syncs: 0 })
    sources[0].open()
    expect(hub.state()).toEqual({ mode: 'push', syncs: 1 })
    sources[0].fail()
    expect(sources[0].closed).toBe(true)
    runTimer()
    sources[1].open()
    expect(hub.state()).toEqual({ mode: 'push', syncs: 2 })
    expect(states.map((s) => s.mode)).toEqual(['push', 'push'])
  })

  it('delivers parsed events to every subscriber and skips bad JSON', () => {
    const { hub, sources } = setup()
    const a = vi.fn()
    const b = vi.fn()
    hub.subscribe({ onEvent: a })
    hub.subscribe({ onEvent: () => { throw new Error('page bug') } })
    hub.subscribe({ onEvent: b })
    sources[0].open()
    sources[0].send('job', { job_id: 'translate_1', status: 'running' })
    sources[0].send('job', '{not json')
    expect(a).toHaveBeenCalledTimes(1)
    expect(a).toHaveBeenCalledWith('job', { job_id: 'translate_1', status: 'running' })
    expect(b).toHaveBeenCalledTimes(1)
  })

  it('turns a server resync into a sync, not an event', () => {
    const { hub, sources } = setup()
    const onEvent = vi.fn()
    hub.subscribe({ onEvent })
    sources[0].open()
    sources[0].send('resync', { topics: ['jobs'] })
    expect(onEvent).not.toHaveBeenCalled()
    expect(hub.state()).toEqual({ mode: 'push', syncs: 2 })
  })

  it(`falls back to polling after ${FALLBACK_AFTER} failures in a row, retrying with capped backoff`, () => {
    const { hub, sources, timers, runTimer } = setup()
    hub.subscribe({})
    sources[0].fail()
    expect(hub.state().mode).toBe('connecting')
    expect(timers[0].ms).toBe(BASE_BACKOFF_MS)
    runTimer()
    sources[1].fail()
    expect(hub.state().mode).toBe('poll')
    expect(timers[0].ms).toBe(BASE_BACKOFF_MS * 2)
    for (let i = 0; i < 12; i++) {
      runTimer()
      sources[sources.length - 1].fail()
    }
    expect(timers[0].ms).toBe(MAX_BACKOFF_MS)
    runTimer()
    sources[sources.length - 1].open()
    expect(hub.state().mode).toBe('push')
    // Backoff starts over after a good open.
    sources[sources.length - 1].fail()
    expect(timers[0].ms).toBe(BASE_BACKOFF_MS)
  })

  it('ignores a closed source and stops retrying without subscribers', () => {
    const { hub, sources, timers } = setup()
    const off = hub.subscribe({})
    const old = sources[0]
    old.fail()
    off()
    expect(timers).toHaveLength(0)
    old.open() // a late callback from the closed source
    expect(hub.state().mode).toBe('connecting')
  })

  it('closes while the tab is hidden (no polling) and reopens with a sync when shown', () => {
    const sources: FakeSource[] = []
    let hidden = false
    let onVis: () => void = () => {}
    const hub = createEventHub({
      create: (url) => {
        const s = new FakeSource(url)
        sources.push(s)
        return s
      },
      setTimer: () => 0,
      clearTimer: () => {},
      isHidden: () => hidden,
      onVisibilityChange: (fn) => (onVis = fn),
    })
    hub.subscribe({})
    sources[0].open()
    expect(hub.state()).toEqual({ mode: 'push', syncs: 1 })
    hidden = true
    onVis()
    expect(sources[0].closed).toBe(true)
    expect(hub.state()).toEqual({ mode: 'connecting', syncs: 1 })
    hidden = false
    onVis()
    expect(sources).toHaveLength(2)
    sources[1].open()
    expect(hub.state()).toEqual({ mode: 'push', syncs: 2 })
  })

  it('does not open while hidden', () => {
    const create = vi.fn()
    const hub = createEventHub({ create, isHidden: () => true, onVisibilityChange: () => {} })
    hub.subscribe({})
    expect(create).not.toHaveBeenCalled()
    expect(hub.state().mode).toBe('connecting')
  })

  it('treats SILENCE_MS without any event as a dropped stream; a ping keeps it alive', () => {
    const { hub, sources, timers } = setup()
    const onEvent = vi.fn()
    hub.subscribe({ onEvent })
    sources[0].open()
    expect(timers.map((t) => t.ms)).toEqual([SILENCE_MS])
    sources[0].send('ping', {})
    expect(onEvent).not.toHaveBeenCalled()
    expect(timers.map((t) => t.ms)).toEqual([SILENCE_MS]) // re-armed, not stacked
    timers.shift()!.fn() // silence
    expect(sources[0].closed).toBe(true)
    expect(timers.map((t) => t.ms)).toEqual([BASE_BACKOFF_MS]) // reconnect scheduled
  })

  it('polls from the start when EventSource is unavailable', () => {
    const hub = createEventHub({ create: null })
    hub.subscribe({})
    expect(hub.state().mode).toBe('poll')
  })

  it('backoffMs doubles and caps', () => {
    expect([1, 2, 3].map(backoffMs)).toEqual([500, 1000, 2000])
    expect(backoffMs(40)).toBe(MAX_BACKOFF_MS)
  })
})
