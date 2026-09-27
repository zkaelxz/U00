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

async function getJson<T>(path: string, fetchImpl: Fetch = fetch): Promise<T> {
  let resp: Response
  try {
    resp = await fetchImpl(`${BASE}${path}`, { headers: { Accept: 'application/json' } })
  } catch {
    throw new ApiError(0, {
      code: 'network_error',
      message: 'Could not reach the Baihe API. Is it running?',
    })
  }
  let body: unknown = null
  try {
    body = await resp.json()
  } catch {
    // Non-JSON body (e.g. a proxy's own error page) -- handled below.
  }
  if (!resp.ok) {
    const info = (body as { error?: ErrorInfo } | null)?.error
    throw new ApiError(
      resp.status,
      info ?? { code: 'internal_error', message: `Request failed (${resp.status}).` },
    )
  }
  return body as T
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
