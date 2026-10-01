/*
 * Client capture for "Report a problem": small ring buffers of what went
 * wrong recently in this tab, bundled into a report on demand.
 *
 *   installCapture()             once, in main.tsx (idempotent)
 *   recordFailedRequest(...)     called by api/client.ts for every failed call
 *   captureSnapshot()            the buffers, for the report
 *
 * Kept: the last 30 console errors/warnings, 30 window errors and unhandled
 * rejections, 30 failed API calls, and the current route plus the last 10
 * route changes.
 *
 * Never kept: request or response bodies, headers, cookies, the CSRF token,
 * query strings, or the contents of objects passed to console (only strings
 * and Error name/message are recorded; any other argument becomes a type tag
 * such as "[object]"). Messages are cut to 500 characters and obvious
 * key/token shapes are masked here; the server redacts everything again.
 */

export type ConsoleEntry = { level: 'error' | 'warn'; message: string; at: string }
export type ErrorEntry = { kind: 'error' | 'unhandledrejection'; message: string; source?: string; at: string }
export type FailedRequest = { method: string; path: string; status: number; code: string; at: string }
export type RouteVisit = { route: string; at: string }

export type CaptureSnapshot = {
  route: string
  route_history: RouteVisit[]
  console: ConsoleEntry[]
  errors: ErrorEntry[]
  failed_requests: FailedRequest[]
}

export const BUFFER_SIZE = 30
export const ROUTE_HISTORY_SIZE = 10
const MESSAGE_MAX = 500

export class Ring<T> {
  private items: T[] = []
  private readonly max: number
  constructor(max: number) {
    this.max = max
  }
  push(item: T) {
    this.items.push(item)
    if (this.items.length > this.max) this.items.splice(0, this.items.length - this.max)
  }
  list(): T[] {
    return [...this.items]
  }
  clear() {
    this.items = []
  }
}

const consoleBuf = new Ring<ConsoleEntry>(BUFFER_SIZE)
const errorBuf = new Ring<ErrorEntry>(BUFFER_SIZE)
const failedBuf = new Ring<FailedRequest>(BUFFER_SIZE)
const routeBuf = new Ring<RouteVisit>(ROUTE_HISTORY_SIZE)
let currentRoute = '/'

function now(): string {
  return new Date().toISOString().slice(11, 19)
}

const SECRET_SHAPES: [RegExp, string][] = [
  [/(baihe_session\s*[=:]\s*)[^;\s"']+/gi, '$1[REDACTED]'],
  [/(x-csrf-token["']?\s*[:=]\s*["']?)[^\s"',;]+/gi, '$1[REDACTED]'],
  [/(authorization["']?\s*[:=]\s*["']?(?:Bearer\s+)?)[A-Za-z0-9_.-]{10,}/gi, '$1[REDACTED]'],
  [/([?&](?:key|token|api_key|access_token)=)[^&\s"']+/gi, '$1[REDACTED]'],
  [/\b(?:sk-|gsk_|hf_|AIza)[A-Za-z0-9_-]{10,}/g, '[REDACTED]'],
  [/(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{32,}(?![A-Za-z0-9_-])/g, '[REDACTED]'],
]

/** Cut to MESSAGE_MAX and mask key/token shapes. */
export function sanitize(text: string, max = MESSAGE_MAX): string {
  let out = text.length > max ? `${text.slice(0, max)}…` : text
  for (const [rx, rep] of SECRET_SHAPES) out = out.replace(rx, rep)
  return out
}

/** One console/rejection argument as safe text: strings and Error name/message only. */
export function describeArg(arg: unknown): string {
  if (typeof arg === 'string') return arg
  if (typeof arg === 'number' || typeof arg === 'boolean' || arg == null) return String(arg)
  if (arg instanceof Error) return `${arg.name}: ${arg.message}`
  return `[${Array.isArray(arg) ? 'array' : typeof arg}]`
}

/** "/api/x/y?q=1#h" or "http://host/api/x?q" -> "/api/x/y" (no origin, query or hash). */
export function stripUrl(url: string): string {
  let path = url.split('#', 1)[0].split('?', 1)[0]
  const m = /^[a-z][a-z0-9+.-]*:\/\/[^/]*(\/.*)?$/i.exec(path)
  if (m) path = m[1] ?? '/'
  return sanitize(path, 300)
}

/** "#/read/3?page=2" -> "/read/3". */
export function routeFromHash(hash: string): string {
  const path = hash.replace(/^#/, '').split('?', 1)[0]
  return sanitize(path.startsWith('/') ? path : `/${path}`, 200)
}

function recordConsole(level: 'error' | 'warn', args: unknown[]) {
  consoleBuf.push({ level, message: sanitize(args.map(describeArg).join(' ')), at: now() })
}

function recordError(kind: 'error' | 'unhandledrejection', message: string, source?: string) {
  errorBuf.push({ kind, message: sanitize(message), ...(source ? { source: stripUrl(source) } : {}), at: now() })
}

export function recordFailedRequest(method: string, url: string, status: number, code: string) {
  failedBuf.push({
    method: (method || 'GET').toUpperCase().slice(0, 10),
    path: stripUrl(url),
    status,
    code: sanitize(code || '', 60),
    at: now(),
  })
}

export function recordRoute(hash: string) {
  const route = routeFromHash(hash)
  if (route === currentRoute && routeBuf.list().length > 0) return
  currentRoute = route
  routeBuf.push({ route, at: now() })
}

export function captureSnapshot(): CaptureSnapshot {
  return {
    route: currentRoute,
    route_history: routeBuf.list(),
    console: consoleBuf.list(),
    errors: errorBuf.list(),
    failed_requests: failedBuf.list(),
  }
}

type ConsoleLike = Pick<Console, 'error' | 'warn'>
type WindowLike = Pick<Window, 'addEventListener'> & { location: { hash: string } }

let installed = false

/** Hooks console.error/warn, window errors, unhandled rejections and route changes. Idempotent. */
export function installCapture(win: WindowLike | undefined = typeof window === 'undefined' ? undefined : window,
  con: ConsoleLike = console): void {
  if (installed || !win) return
  installed = true
  for (const level of ['error', 'warn'] as const) {
    const original = con[level].bind(con)
    con[level] = (...args: unknown[]) => {
      try {
        recordConsole(level, args)
      } catch {
        // Capture must never break logging.
      }
      original(...args)
    }
  }
  win.addEventListener('error', (e: Event) => {
    const ev = e as ErrorEvent
    const where = ev.filename ? `${ev.filename}:${ev.lineno ?? 0}:${ev.colno ?? 0}` : undefined
    recordError('error', ev.error ? describeArg(ev.error) : String(ev.message ?? 'error'), where)
  })
  win.addEventListener('unhandledrejection', (e: Event) => {
    recordError('unhandledrejection', describeArg((e as PromiseRejectionEvent).reason))
  })
  win.addEventListener('hashchange', () => recordRoute(win.location.hash))
  recordRoute(win.location.hash)
}

/** Test-only: empty every buffer and allow installCapture again. */
export function resetCaptureForTests(): void {
  consoleBuf.clear()
  errorBuf.clear()
  failedBuf.clear()
  routeBuf.clear()
  currentRoute = '/'
  installed = false
}
