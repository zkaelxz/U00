// Pasted-URL extraction extras (Sources parity SO09, SO06).
//
//   GET  /api/sources/url/ai-engines  -> {engines, default}           (sources.import)
//   POST /api/sources/url/import       {url, drama_id, use_ai?, engine?} -> {job_id: 'sourceimport_<drama>'}
//   POST /api/sources/url/import-comic {url, drama_id, use_ai?, engine?} -> same job id (comic dramas)
//
// A paid engine also needs `engines.paid` (403 otherwise). The key stays on
// the PC; the browser only ever names the engine.
import type { SourcesJobStarted } from '../types/sources'
import type { AiEngines, AiRequestFields } from '../types/sourcesExtraction'
import { getJson, postJson } from './client'

type Fetch = typeof fetch

export const getAiEngines = (f?: Fetch) => getJson<AiEngines>('/api/sources/url/ai-engines', f)

export const startNovelUrlImport = (url: string, drama_id: number, ai: AiRequestFields, f?: Fetch) =>
  postJson<SourcesJobStarted>('/api/sources/url/import', { url, drama_id, ...ai }, f)

export const startComicUrlImport = (url: string, drama_id: number, ai: AiRequestFields, f?: Fetch) =>
  postJson<SourcesJobStarted>('/api/sources/url/import-comic', { url, drama_id, ...ai }, f)
