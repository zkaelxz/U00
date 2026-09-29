import type {
  ArtifactInfo,
  AssExportRequest,
  AssStyleOptions,
  AutoQcFlagResult,
  ExportReadiness,
  FlagActionResult,
  MediaExportStarted,
  MediaKind,
  SubtitleOptions,
} from '../types/export'
import { ApiError, apiUrl, getJson, postJson } from './client'
import type { ErrorInfo } from './types'

type Fetch = typeof fetch

const dramaPath = (id: number) => `/api/export/dramas/${id}`

// Text/binary responses: client.ts's helpers only parse JSON, so errors
// (which are JSON) are decoded here into the same ApiError.
async function fetchBody<T>(
  path: string,
  init: RequestInit,
  read: (r: Response) => Promise<T>,
  f: Fetch,
): Promise<T> {
  let resp: Response
  try {
    resp = await f(apiUrl(path), init)
  } catch {
    throw new ApiError(0, {
      code: 'network_error',
      message: 'Could not reach the Baihe API. Is it running?',
    })
  }
  if (!resp.ok) {
    let info: ErrorInfo | undefined
    try {
      info = ((await resp.json()) as { error?: ErrorInfo }).error
    } catch {
      // not JSON
    }
    throw new ApiError(
      resp.status,
      info ?? { code: 'internal_error', message: `Request failed (${resp.status}).` },
    )
  }
  return read(resp)
}

export function subtitleQuery(o: SubtitleOptions): string {
  const p = new URLSearchParams({ fmt: o.fmt, field: o.field })
  if (o.includeNotes) p.set('include_notes', 'true')
  if (o.wrapEn !== undefined) p.set('wrap_chars_en', String(o.wrapEn))
  if (o.wrapSource !== undefined) p.set('wrap_chars_source', String(o.wrapSource))
  return p.toString()
}

export const getReadiness = (id: number, f?: Fetch) =>
  getJson<ExportReadiness>(`${dramaPath(id)}/readiness`, f)
export const getAssStyleOptions = (f?: Fetch) =>
  getJson<AssStyleOptions>('/api/export/ass-style-options', f)
export const getSubtitleText = (id: number, o: SubtitleOptions, f: Fetch = fetch) =>
  fetchBody(
    `${dramaPath(id)}/subtitle?${subtitleQuery(o)}`,
    { headers: { Accept: 'text/plain' } },
    (r) => r.text(),
    f,
  )
export const getAssText = (id: number, req: AssExportRequest, f: Fetch = fetch) =>
  fetchBody(
    `${dramaPath(id)}/ass`,
    {
      method: 'POST',
      headers: { Accept: 'text/plain', 'Content-Type': 'application/json' },
      body: JSON.stringify(req),
    },
    (r) => r.text(),
    f,
  )
export const getEpub = (id: number, field: 'en' | 'zh', f: Fetch = fetch) =>
  fetchBody(`${dramaPath(id)}/epub?field=${field}`, {}, (r) => r.blob(), f)
export const flagOverlaps = (id: number, f?: Fetch) =>
  postJson<FlagActionResult>(`${dramaPath(id)}/flag-overlaps`, undefined, f)
export const flagDenseLines = (id: number, f?: Fetch) =>
  postJson<FlagActionResult>(`${dramaPath(id)}/flag-dense-lines`, undefined, f)
export const flagAutoQc = (id: number, f?: Fetch) =>
  postJson<AutoQcFlagResult>(`${dramaPath(id)}/flag-auto-qc`, undefined, f)
export const startAudiobook = (id: number, f?: Fetch) =>
  postJson<MediaExportStarted>(`${dramaPath(id)}/audiobook`, undefined, f)
export const startBurnedVideo = (id: number, req: AssExportRequest, f?: Fetch) =>
  postJson<MediaExportStarted>(`${dramaPath(id)}/burned-video`, req, f)
export const getArtifactInfo = (id: number, kind: MediaKind, f?: Fetch) =>
  getJson<ArtifactInfo>(`/api/artifacts/dramas/${id}/${encodeURIComponent(kind)}/info`, f)
