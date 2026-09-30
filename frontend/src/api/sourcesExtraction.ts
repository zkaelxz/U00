// Pasted-URL extraction extras (Sources parity SO09, SO06, SO10).
//
//   GET  /api/sources/url/ai-engines  -> {engines, default}           (sources.import)
//   POST /api/sources/url/import       {url, drama_id, use_ai?, engine?} -> {job_id: 'sourceimport_<drama>'}
//   POST /api/sources/url/import-comic {url, drama_id, use_ai?, engine?} -> same job id (comic dramas)
//   (both also take review: true -> open a Review extraction instead of writing)
//
// Review extraction, per drama (sources.import unless marked PC only):
//   GET  /api/sources/dramas/{id}/extraction                      -> ExtractionReview (404: none)
//   POST .../extraction/rerun-novel   NovelRerunRequest           -> ExtractionReview
//   POST .../extraction/rerun-comic   {revision, images}          -> ExtractionReview
//   POST .../extraction/save-profile  {revision}   PC only        -> ProfileSaved
//   POST .../extraction/approve-profile {revision} PC only        -> ProfileSaved
//   POST .../extraction/import        {revision}                  -> {job_id: 'sourceimport_<drama>'}
//   GET  .../extraction/images/{id}   a thumbnail (raster image)
// A stale revision is a 409: reload the review.
//
// A paid engine also needs `engines.paid` (403 otherwise). The key stays on
// the PC; the browser only ever names the engine.
import type { SourcesJobStarted } from '../types/sources'
import type {
  AiEngines, AiRequestFields, ExtractionReview, ImageChoice, NovelRerunRequest, ProfileSaved,
} from '../types/sourcesExtraction'
import { apiUrl, getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

export const getAiEngines = (f?: Fetch) => getJson<AiEngines>('/api/sources/url/ai-engines', f)

export const startNovelUrlImport = (url: string, drama_id: number, ai: AiRequestFields, f?: Fetch) =>
  postJson<SourcesJobStarted>('/api/sources/url/import', { url, drama_id, ...ai }, f)

export const startComicUrlImport = (url: string, drama_id: number, ai: AiRequestFields, f?: Fetch) =>
  postJson<SourcesJobStarted>('/api/sources/url/import-comic', { url, drama_id, ...ai }, f)

const review = (dramaId: number) => `/api/sources/dramas/${dramaId}/extraction`

export const getExtractionReview = (dramaId: number, f?: Fetch) => getJson<ExtractionReview>(review(dramaId), f)

export const rerunNovel = (dramaId: number, body: NovelRerunRequest, f?: Fetch) =>
  postJson<ExtractionReview>(`${review(dramaId)}/rerun-novel`, body, f)

export const rerunComic = (dramaId: number, revision: string, images: ImageChoice[], f?: Fetch) =>
  postJson<ExtractionReview>(`${review(dramaId)}/rerun-comic`, { revision, images }, f)

export const saveReviewProfile = (dramaId: number, revision: string, f?: Fetch) =>
  postJson<ProfileSaved>(`${review(dramaId)}/save-profile`, { revision }, pcOnlyFetch(f))

export const approveReviewProfile = (dramaId: number, revision: string, f?: Fetch) =>
  postJson<ProfileSaved>(`${review(dramaId)}/approve-profile`, { revision }, pcOnlyFetch(f))

export const startReviewImport = (dramaId: number, revision: string, f?: Fetch) =>
  postJson<SourcesJobStarted>(`${review(dramaId)}/import`, { revision }, f)

export const reviewImageUrl = (dramaId: number, imageId: number) => apiUrl(`${review(dramaId)}/images/${imageId}`)
