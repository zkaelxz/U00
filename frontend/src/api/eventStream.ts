/*
 * One shared server-sent events stream per tab (GET /api/events,
 * api/routers/events_routes.py): job changes, the header bell's list and
 * Live session status are pushed instead of polled. Subscribers share the
 * one EventSource; it opens with the first subscriber and closes with the
 * last.
 *
 * Modes: 'connecting' until the first open, 'push' while open, 'poll' when
 * the stream is unavailable (no EventSource, or it failed FALLBACK_AFTER
 * times in a row). In 'poll' each page runs its old polling loop; the hub
 * keeps retrying with capped exponential backoff, and every open (and a
 * server 'resync') bumps `syncs` so pages re-read once with a GET. The
 * browser's own auto-reconnect is not used: an HTTP error (401, 429) would
 * stop it for good, and the backoff should be ours.
 *
 * A stream silent for SILENCE_MS (the server pings every 15 s) counts as
 * dropped, so a stalled one falls back like any other failure.
 *
 * While the tab is hidden the stream is closed (no polling either) and it
 * reopens, with a sync, when the tab is shown: a background tab never keeps
 * an idle sign-in alive, and a hidden job page catches up on return.
 *
 * Framework-free and injectable (EventSource factory, timers, visibility), so the
 * reconnect and fallback rules are unit-tested with fakes.
 */

import { apiUrl } from './client'

export type StreamMode = 'connecting' | 'push' | 'poll'

const EVENTS_PATH = '/api/events?topics=jobs,notifications,live'
const STREAM_EVENTS = ['job', 'job_gone', 'notifications', 'live', 'resync', 'ping'] as const
export const FALLBACK_AFTER = 2
export const BASE_BACKOFF_MS = 500
export const MAX_BACKOFF_MS = 30_000
// The server sends a 'ping' after 15 s of silence; this long with nothing at
// all means the stream stalled (a proxy holding it open): treat it as dropped.
export const SILENCE_MS = 45_000

export interface StreamState {
  mode: StreamMode
  // Bumped on every open and on a server 'resync': time to re-read with one
  // GET (a page that mounted before the first open reads twice, once).
  syncs: number
}

export interface StreamListener {
  onEvent?: (type: string, data: unknown) => void
  onState?: (state: StreamState) => void
}

// The part of EventSource the hub uses.
export interface EventSourceLike {
  onopen: ((ev: Event) => unknown) | null
  onerror: ((ev: Event) => unknown) | null
  addEventListener(type: string, fn: (ev: MessageEvent) => void): void
  close(): void
}

interface HubDeps {
  url?: string
  create?: ((url: string) => EventSourceLike) | null
  setTimer?: (fn: () => void, ms: number) => unknown
  clearTimer?: (t: unknown) => void
  // Page visibility: the stream is closed while the tab is hidden (so a
  // background tab never keeps an idle sign-in alive) and reopened, with a
  // sync, when it is shown again.
  isHidden?: () => boolean
  onVisibilityChange?: (fn: () => void) => void
}

export interface EventHub {
  subscribe(listener: StreamListener): () => void
  state(): StreamState
}

export function backoffMs(failures: number): number {
  return Math.min(BASE_BACKOFF_MS * 2 ** Math.max(0, failures - 1), MAX_BACKOFF_MS)
}

function defaultCreate(): ((url: string) => EventSourceLike) | null {
  if (typeof window === 'undefined' || typeof window.EventSource !== 'function') return null
  return (url) => new window.EventSource(url) as unknown as EventSourceLike
}

export function createEventHub(deps: HubDeps = {}): EventHub {
  const url = deps.url ?? apiUrl(EVENTS_PATH)
  const create = deps.create === undefined ? defaultCreate() : deps.create
  const setTimer = deps.setTimer ?? ((fn, ms) => setTimeout(fn, ms))
  const clearTimer = deps.clearTimer ?? ((t) => clearTimeout(t as ReturnType<typeof setTimeout>))
  const hasDocument = typeof document !== 'undefined'
  const isHidden = deps.isHidden ?? (() => hasDocument && document.visibilityState === 'hidden')
  const onVisibilityChange =
    deps.onVisibilityChange ??
    ((fn: () => void) => {
      if (hasDocument) document.addEventListener('visibilitychange', fn)
    })

  const listeners = new Set<StreamListener>()
  let state: StreamState = { mode: create ? 'connecting' : 'poll', syncs: 0 }
  let source: EventSourceLike | null = null
  let timer: unknown = null
  let failures = 0

  const setState = (next: StreamState) => {
    if (next.mode === state.mode && next.syncs === state.syncs) return
    state = next
    for (const l of [...listeners]) l.onState?.(state)
  }

  const emit = (type: string, data: unknown) => {
    for (const l of [...listeners]) {
      try {
        l.onEvent?.(type, data)
      } catch {
        // One page's handler must not starve the others.
      }
    }
  }

  // Watchdog: restarted by the open and by every event, fires after SILENCE_MS.
  let watchdog: unknown = null
  const clearWatchdog = () => {
    if (watchdog !== null) clearTimer(watchdog)
    watchdog = null
  }
  const armWatchdog = (es: EventSourceLike) => {
    clearWatchdog()
    watchdog = setTimer(() => {
      watchdog = null
      if (source === es) es.onerror?.(new Event('error'))
    }, SILENCE_MS)
  }

  const stop = () => {
    if (timer !== null) clearTimer(timer)
    timer = null
    clearWatchdog()
    if (source) {
      source.onopen = null
      source.onerror = null
      source.close()
    }
    source = null
  }

  const connect = () => {
    timer = null
    if (!create || listeners.size === 0 || isHidden()) return
    let es: EventSourceLike
    try {
      es = create(url)
    } catch {
      failed()
      return
    }
    source = es
    es.onopen = () => {
      if (source !== es) return
      failures = 0
      armWatchdog(es)
      // Every open is a sync: anything that changed before it was missed.
      setState({ mode: 'push', syncs: state.syncs + 1 })
    }
    es.onerror = () => {
      if (source !== es) return
      clearWatchdog()
      es.onopen = null
      es.onerror = null
      es.close()
      source = null
      failed()
    }
    for (const type of STREAM_EVENTS) {
      es.addEventListener(type, (ev: MessageEvent) => {
        if (source !== es) return
        armWatchdog(es)
        if (type === 'ping') return
        let data: unknown
        try {
          data = JSON.parse(String(ev.data))
        } catch {
          return
        }
        if (type === 'resync') setState({ mode: state.mode, syncs: state.syncs + 1 })
        else emit(type, data)
      })
    }
  }

  const failed = () => {
    failures += 1
    if (failures >= FALLBACK_AFTER) setState({ mode: 'poll', syncs: state.syncs })
    if (listeners.size > 0) timer = setTimer(connect, backoffMs(failures))
  }

  if (create) {
    onVisibilityChange(() => {
      if (isHidden()) {
        if (!source && timer === null) return
        // Hidden: no stream and no polling; the next open re-reads.
        stop()
        failures = 0
        setState({ mode: 'connecting', syncs: state.syncs })
      } else if (listeners.size > 0 && !source && timer === null) {
        connect()
      }
    })
  }

  return {
    subscribe(listener) {
      listeners.add(listener)
      if (listeners.size === 1 && create && !source && timer === null) connect()
      return () => {
        listeners.delete(listener)
        if (listeners.size === 0) {
          stop()
          failures = 0
          // The next subscriber starts from a fresh connection.
          if (create) state = { mode: 'connecting', syncs: state.syncs }
        }
      }
    },
    state: () => state,
  }
}

let shared: EventHub | null = null

/** The tab's one hub (created on first use). */
export function eventHub(): EventHub {
  if (!shared) shared = createEventHub()
  return shared
}
