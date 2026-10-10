import type {
  ArtifactInfo,
  AssExportRequest,
  AssStyleOptions,
  AutoQcFlagResult,
  ClearReadingSpeedFlagsResult,
  DubbedVideoRequest,
  ExportReadiness,
  FlagActionResult,
  MarkExportedResult,
  MediaExportStarted,
  MediaKind,
  ReadingSpeedMode,
  ReadingSpeedModeResult,
  SoftsubVideoRequest,
  SubtitleOptions,
} from '../types/export'
import { fetchBody, getJson, postJson } from './client'

type Fetch = typeof fetch

const dramaPath = (id: number) => `/api/export/dramas/${id}`

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
/** The file's text plus the name the server chose for it (null if it sent none). */
export interface SubtitleFile {
  text: string
  filename: string | null
}

/** The name in a Content-Disposition header: `filename*=` (RFC 5987, UTF-8) first, then `filename=`. */
export function dispositionFilename(header: string | null): string | null {
  if (!header) return null
  const star = /filename\*\s*=\s*([^;]+)/i.exec(header)
  if (star) {
    const m = /^(?:[\w-]+)'[^']*'(.*)$/.exec(star[1].trim())
    if (m) {
      try {
        const name = decodeURIComponent(m[1])
        if (name) return name
      } catch {
        // A malformed escape falls through to the plain filename.
      }
    }
  }
  const plain = /filename\s*=\s*(?:"((?:[^"\\]|\\.)*)"|([^;]+))/i.exec(header.replace(/filename\*\s*=\s*[^;]+/gi, ''))
  const name = (plain?.[1]?.replace(/\\(.)/g, '$1') ?? plain?.[2]?.trim()) || null
  return name
}

const readSubtitleFile = async (r: Response): Promise<SubtitleFile> => ({
  text: await r.text(),
  filename: dispositionFilename(r.headers.get('Content-Disposition')),
})

export const getSubtitleText = (id: number, o: SubtitleOptions, f?: Fetch) =>
  fetchBody(
    `${dramaPath(id)}/subtitle?${subtitleQuery(o)}`,
    { headers: { Accept: 'text/plain' } },
    readSubtitleFile,
    f,
  )
export const getAssText = (id: number, req: AssExportRequest, f?: Fetch) =>
  fetchBody(
    `${dramaPath(id)}/ass`,
    {
      method: 'POST',
      headers: { Accept: 'text/plain', 'Content-Type': 'application/json' },
      body: JSON.stringify(req),
    },
    readSubtitleFile,
    f,
  )
export const getEpub = (id: number, field: 'en' | 'zh', f?: Fetch) =>
  fetchBody(`${dramaPath(id)}/epub?field=${field}`, {}, (r) => r.blob(), f)
export const flagOverlaps = (id: number, f?: Fetch) =>
  postJson<FlagActionResult>(`${dramaPath(id)}/flag-overlaps`, undefined, f)
export const flagDenseLines = (id: number, f?: Fetch) =>
  postJson<FlagActionResult>(`${dramaPath(id)}/flag-dense-lines`, undefined, f)
export const getReadingSpeedMode = (id: number, f?: Fetch) =>
  getJson<ReadingSpeedModeResult>(`${dramaPath(id)}/reading-speed`, f)
export const setReadingSpeedMode = (id: number, mode: ReadingSpeedMode, f?: Fetch) =>
  postJson<ReadingSpeedModeResult>(`${dramaPath(id)}/reading-speed`, { mode }, f)
export const clearReadingSpeedFlags = (id: number, recheck: boolean, f?: Fetch) =>
  postJson<ClearReadingSpeedFlagsResult>(
    `${dramaPath(id)}/clear-reading-speed-flags${recheck ? '?recheck=true' : ''}`, undefined, f)
export const flagAutoQc = (id: number, f?: Fetch) =>
  postJson<AutoQcFlagResult>(`${dramaPath(id)}/flag-auto-qc`, undefined, f)
export const startAudiobook = (id: number, f?: Fetch) =>
  postJson<MediaExportStarted>(`${dramaPath(id)}/audiobook`, undefined, f)
export const startBurnedVideo = (id: number, req: AssExportRequest, f?: Fetch) =>
  postJson<MediaExportStarted>(`${dramaPath(id)}/burned-video`, req, f)
export const startSoftsubVideo = (id: number, req: SoftsubVideoRequest, f?: Fetch) =>
  postJson<MediaExportStarted>(`${dramaPath(id)}/softsub-video`, req, f)
export const startDubbedVideo = (id: number, req: DubbedVideoRequest, f?: Fetch) =>
  postJson<MediaExportStarted>(`${dramaPath(id)}/dubbed-video`, req, f)
export const markExported = (id: number, f?: Fetch) =>
  postJson<MarkExportedResult>(`${dramaPath(id)}/mark-exported`, undefined, f)
export const getArtifactInfo = (id: number, kind: MediaKind, f?: Fetch) =>
  getJson<ArtifactInfo>(`/api/artifacts/dramas/${id}/${encodeURIComponent(kind)}/info`, f)
