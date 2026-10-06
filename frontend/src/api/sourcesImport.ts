// Sources import (contract: docs/specs/discover-sources-live-api-spec.md, S-4/S-5).
//
//   POST /api/sources/url/preview            {url}                 -> {job_id: 'sources_url_preview'}
//   POST /api/sources/url/import             {url, drama_id}       -> {job_id: 'sourceimport_<drama>'}
//      (sent by startNovelUrlImport in sourcesExtraction.ts, with the AI fallback fields)
//   POST /api/sources/{name}/import          {series_id, chapter_ids, drama_id} -> same job id
//      POST /api/sources/{name}/save            {series_id, chapter_ids} -> {job_id: 'sources_save'}
//   POST /api/sources/tracked                {source, series_id, tracked: true, drama_id?} -> TrackedSeries[]
//   POST /api/sources/{name}/import/{chapter_id}/ai-recover {series_id, drama_id, engine, confirm} -> same job id
//   GET /api/sources/{name}/import-state?series_id=&drama_id= -> ImportState
//   POST /api/media/dramas/{id}/download-url {url, audio_only, confirm_replace_audio} -> {job_id: 'urlmedia_<drama>'}
//
// All but the last (download-url) are `sources.import`; their job results come from
// GET /api/sources/jobs/{id}/result (useSourcesJob). download-url is PC-only
// (local_only), so it goes through pcOnlyFetch; its job is read with
// GET /api/jobs/{id}. Chapter imports send ids only, never URLs or objects.
import type { SourcesJobStarted, TrackedSeries } from '../types/sources'
import type { AiRecoverRequest, ChapterImportRequest, ChapterSaveRequest, ImportState, UrlDownloadRequest } from '../types/sourcesImport'
import { getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

const seg = (s: string) => encodeURIComponent(s)

export const URL_PREVIEW_JOB_ID = 'sources_url_preview'
export const sourceImportJobId = (dramaId: number) => `sourceimport_${dramaId}`
export const SAVE_JOB_ID = 'sources_save'
export const urlMediaJobId = (dramaId: number) => `urlmedia_${dramaId}`

export const startUrlPreview = (url: string, f?: Fetch) =>
  postJson<SourcesJobStarted>('/api/sources/url/preview', { url }, f)

export const startChapterImport = (source: string, body: ChapterImportRequest, f?: Fetch) =>
  postJson<SourcesJobStarted>(`/api/sources/${seg(source)}/import`, body, f)

/** Saves the chosen chapters of a comic series as CBZ files on the PC (one save at a time). */
export const startChapterSave = (source: string, body: ChapterSaveRequest, f?: Fetch) =>
  postJson<SourcesJobStarted>(`/api/sources/${seg(source)}/save`, body, f)

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
