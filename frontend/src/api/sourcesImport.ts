// Sources import (docs spec s4-s5-url-import §2).
//
//   R1 POST /api/sources/url/preview            {url}                 -> {job_id: 'sources_url_preview'}
//   R2 POST /api/sources/url/import             {url, drama_id}       -> {job_id: 'sourceimport_<drama>'}
//      (sent by startNovelUrlImport in sourcesExtraction.ts, with the AI fallback fields)
//   R3 POST /api/sources/{name}/import          {series_id, chapter_ids, drama_id} -> same job id
//   R4 POST /api/sources/tracked                {source, series_id, tracked: true, drama_id?} -> TrackedSeries[]
//   POST /api/sources/{name}/import/{chapter_id}/ai-recover {series_id, drama_id, engine, confirm} -> same job id
//   GET /api/sources/{name}/import-state?series_id=&drama_id= -> ImportState (Step 107)
//   R5 POST /api/media/dramas/{id}/download-url {url, audio_only, confirm_replace_audio} -> {job_id: 'urlmedia_<drama>'}
//
// R1-R4 are `sources.import`; their job results come from
// GET /api/sources/jobs/{id}/result (useSourcesJob). R5 is PC-only
// (local_only), so it goes through pcOnlyFetch; its job is read with
// GET /api/jobs/{id}. Chapter imports send ids only, never URLs or objects.
import type { SourcesJobStarted, TrackedSeries } from '../types/sources'
import type { AiRecoverRequest, ChapterImportRequest, ImportState, UrlDownloadRequest } from '../types/sourcesImport'
import { getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

const seg = (s: string) => encodeURIComponent(s)

export const URL_PREVIEW_JOB_ID = 'sources_url_preview'
export const sourceImportJobId = (dramaId: number) => `sourceimport_${dramaId}`
export const urlMediaJobId = (dramaId: number) => `urlmedia_${dramaId}`

export const startUrlPreview = (url: string, f?: Fetch) =>
  postJson<SourcesJobStarted>('/api/sources/url/preview', { url }, f)

export const startChapterImport = (source: string, body: ChapterImportRequest, f?: Fetch) =>
  postJson<SourcesJobStarted>(`/api/sources/${seg(source)}/import`, body, f)

/** One AI call on a chapter whose page layout changed; the job ends in a Review extraction. */
export const startAiRecover = (source: string, chapterId: string, body: AiRecoverRequest, f?: Fetch) =>
  postJson<SourcesJobStarted>(`/api/sources/${seg(source)}/import/${seg(chapterId)}/ai-recover`, body, f)

/** Chapters already imported into the drama, and the ones to retry (reads only). */
export function getImportState(source: string, seriesId: string, dramaId: number, f?: Fetch) {
  const q = new URLSearchParams({ series_id: seriesId, drama_id: String(dramaId) })
  return getJson<ImportState>(`/api/sources/${seg(source)}/import-state?${q}`, f)
}

export function trackSeries(source: string, series_id: string, drama_id?: number | null, f?: Fetch) {
  const body = drama_id ? { source, series_id, tracked: true, drama_id } : { source, series_id, tracked: true }
  return postJson<TrackedSeries[]>('/api/sources/tracked', body, f)
}

export const startUrlDownload = (dramaId: number, body: UrlDownloadRequest, f?: Fetch) =>
  postJson<SourcesJobStarted>(`/api/media/dramas/${dramaId}/download-url`, body, pcOnlyFetch(f))
