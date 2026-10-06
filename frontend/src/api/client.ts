// The only place the frontend talks HTTP. Components call these typed
// functions; they never build URLs or parse error bodies themselves.

import type {
  DramaDetail,
  DramaFilters,
  DramaListResponse,
  ErrorInfo,
  HealthResponse,
  MetaResponse,
} from './types'
import { recordFailedRequest } from '../report/capture'

// Relative by default: the Vite dev/preview proxy (vite.config.ts) or a
// same-origin deployment forwards /api to FastAPI. VITE_API_BASE_URL is
// only for pointing a dev build straight at another origin, which then
// needs that origin in the API's BAIHE_API_CORS_ORIGINS.
const BASE = (import.meta.env?.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly details?: unknown

  constructor(status: number, info: ErrorInfo) {
    super(info.message)
    this.name = 'ApiError'
    this.status = status
    this.code = info.code
    this.details = info.details
  }
}

type Fetch = typeof fetch

// ---- CSRF (auth on) ----
// With sign-in on, the server sets a readable CSRF cookie next to the
// HttpOnly session cookie: `__Host-baihe_csrf` over https, `baihe_csrf` in
// plain-http dev. Every mutating request echoes it back as X-CSRF-Token
// (double submit; the server also checks it against the session). With
// auth off there is no cookie and no header.
const CSRF_COOKIES = ['__Host-baihe_csrf', 'baihe_csrf']
const MUTATING = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])

export function readCsrfToken(
  cookies: string = typeof document === 'undefined' ? '' : (document.cookie ?? ''),
): string | null {
  const jar = new Map<string, string>()
  for (const part of cookies.split(';')) {
    const eq = part.indexOf('=')
    if (eq < 0) continue
    const name = part.slice(0, eq).trim()
    if (!jar.has(name)) jar.set(name, part.slice(eq + 1).trim())
  }
  for (const name of CSRF_COOKIES) {
    const raw = jar.get(name)
    if (!raw) continue
    try {
      return decodeURIComponent(raw)
    } catch {
      return raw
    }
  }
  return null
}

/** `{ 'X-CSRF-Token': token }` when a CSRF cookie is present, else `{}`. */
export function csrfHeader(): Record<string, string> {
  const token = readCsrfToken()
  return token ? { 'X-CSRF-Token': token } : {}
}

export function isMutating(method: string | undefined): boolean {
  return MUTATING.has((method ?? 'GET').toUpperCase())
}

// ---- 401: signed out ----
// Any 401 means the session is gone (expired, revoked, or signed out in
// another tab). hooks/useSession.ts subscribes and swaps in the Login page.
const unauthorizedListeners = new Set<() => void>()

export function onUnauthorized(listener: () => void): () => void {
  unauthorizedListeners.add(listener)
  return () => unauthorizedListeners.delete(listener)
}

function withCsrf(init: RequestInit): RequestInit {
  if (!isMutating(init.method)) return init
  const extra = csrfHeader()
  if (!extra['X-CSRF-Token']) return init
  return { ...init, headers: { ...(init.headers as Record<string, string> | undefined), ...extra } }
}

// Every request goes through here: the CSRF header on mutations, one
// network-error shape, and the 401 -> signed-out notification.
async function send(path: string, init: RequestInit, fetchImpl: Fetch): Promise<Response> {
  let resp: Response
  try {
    resp = await fetchImpl(`${BASE}${path}`, withCsrf(init))
  } catch (e) {
    // A superseded pan/zoom request is not a failure worth a report slot.
    if ((e as { name?: string } | null)?.name !== 'AbortError') {
      recordFailedRequest(init.method ?? 'GET', path, 0, 'network_error')
    }
    throw new ApiError(0, {
      code: 'network_error',
      message: 'Could not reach the Baihe API. Is it running?',
    })
  }
  if (resp.status === 401) unauthorizedListeners.forEach((l) => l())
  return resp
}

// Records the failure for "Report a problem" (method, path without query,
// status and code only: never bodies or headers) and returns the error.
function failure(method: string | undefined, path: string, status: number, body: unknown): ApiError {
  const info = (body as { error?: ErrorInfo } | null)?.error
  // Peaks answers 429 while a decode is running and the waveform retries on its
  // own; recording each would push real failures out of the bounded buffer.
  if (!(status === 429 && path.includes('/peaks'))) {
    recordFailedRequest(method ?? 'GET', path, status, info?.code ?? 'internal_error')
  }
  return new ApiError(status, info ?? { code: 'internal_error', message: `Request failed (${status}).` })
}

async function request<T>(path: string, init: RequestInit, fetchImpl: Fetch): Promise<T> {
  const resp = await send(path, init, fetchImpl)
  let body: unknown = null
  try {
    body = await resp.json()
  } catch {
    // Non-JSON body (e.g. a proxy's own error page) -- handled below.
  }
  if (!resp.ok) throw failure(init.method, path, resp.status, body)
  return body as T
}

/**
 * For text/binary responses (subtitle text, an EPUB blob): same CSRF,
 * X-Baihe-Local on mutations, 401 and error handling as the JSON helpers,
 * but the caller reads the body. Errors (which are JSON) become ApiError.
 */
export async function fetchBody<T>(
  path: string,
  init: RequestInit,
  read: (r: Response) => Promise<T>,
  fetchImpl: Fetch = fetch,
): Promise<T> {
  const headers = isMutating(init.method)
    ? { ...(init.headers as Record<string, string> | undefined), ...LOCAL_HEADER }
    : init.headers
  const resp = await send(path, { ...init, headers }, fetchImpl)
  if (!resp.ok) {
    let body: unknown = null
    try {
      body = await resp.json()
    } catch {
      // not JSON
    }
    throw failure(init.method, path, resp.status, body)
  }
  return read(resp)
}

const JSON_ACCEPT = { Accept: 'application/json' }

// A fetch that aborts with `signal`, for api functions that take a Fetch.
export function withSignal(signal: AbortSignal, fetchImpl: Fetch = fetch): Fetch {
  return (input, init) => fetchImpl(input, { ...init, signal })
}

// HEAD status of a full API URL (no body read; a 401 here is reported, not
// treated as signed out). A network failure throws.
export async function headStatus(url: string, fetchImpl: Fetch = fetch): Promise<number> {
  return (await fetchImpl(url, { method: 'HEAD' })).status
}

export function getJson<T>(path: string, fetchImpl: Fetch = fetch): Promise<T> {
  return request<T>(path, { headers: JSON_ACCEPT }, fetchImpl)
}

export function postJson<T>(path: string, body?: unknown, fetchImpl: Fetch = fetch): Promise<T> {
  return request<T>(
    path,
    {
      method: 'POST',
      headers:
        body === undefined
          ? { ...JSON_ACCEPT, ...LOCAL_HEADER }
          : { ...JSON_ACCEPT, ...LOCAL_HEADER, 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    },
    fetchImpl,
  )
}

export function deleteJson<T>(path: string, fetchImpl: Fetch = fetch): Promise<T> {
  return request<T>(path, { method: 'DELETE', headers: { ...JSON_ACCEPT, ...LOCAL_HEADER } }, fetchImpl)
}

// Sent on every mutating request (plus X-CSRF-Token when signed in, see
// withCsrf above). With auth off the server refuses a
// POST/PUT/PATCH that is neither JSON nor carries this header (bodyless
// POSTs and multipart are CORS "simple" requests); the custom header forces
// a preflight, which stops a page on another local port from posting.
export const LOCAL_HEADER = { 'X-Baihe-Local': '1' }

// Multipart upload. No Content-Type header: the browser sets it with the boundary.
export function postMultipart<T>(
  path: string,
  form: FormData,
  fetchImpl: Fetch = fetch,
): Promise<T> {
  return request<T>(
    path,
    { method: 'POST', headers: { ...JSON_ACCEPT, ...LOCAL_HEADER }, body: form },
    fetchImpl,
  )
}

// The full URL for an API path ("/api/..."), for links and for the few
// callers that fetch non-JSON bodies themselves.
export function apiUrl(path: string): string {
  return `${BASE}${path}`
}

// A plain link for the browser to download (Content-Disposition: attachment);
// nothing is fetched or buffered in JS.
export function artifactUrl(dramaId: number, kind: string): string {
  return apiUrl(`/api/artifacts/dramas/${dramaId}/${encodeURIComponent(kind)}`)
}

export function buildDramaQuery(filters: DramaFilters = {}): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(filters)) {
    if (Array.isArray(value)) value.forEach((v) => v && params.append(key, v))
    else if (value) params.set(key, value)
  }
  const qs = params.toString()
  return qs ? `?${qs}` : ''
}

export const api = {
  health: (f?: Fetch) => getJson<HealthResponse>('/api/health', f),
  meta: (f?: Fetch) => getJson<MetaResponse>('/api/meta', f),
  listDramas: (filters?: DramaFilters, f?: Fetch) =>
    getJson<DramaListResponse>(`/api/library/dramas${buildDramaQuery(filters)}`, f),
  getDrama: (id: number, f?: Fetch) => getJson<DramaDetail>(`/api/library/dramas/${id}`, f),
}
