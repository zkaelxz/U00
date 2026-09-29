// Sources page: the registry (api/routers/sources_catalog_routes.py) and the
// paced search/series jobs (api/routers/sources_search_routes.py).
//
// Search, series and job results are `library.read`. Dismiss/untrack are
// `sources.import`. Everything in Source settings (switches, health reset,
// cache, profiles, pacing) is admin/PC-only, so it goes through pcOnlyFetch
// (X-Baihe-Local; a 403 marks the tab remote). Bodies never carry URLs:
// search takes text and source names, series takes an id.
import type {
  SourceAttempt,
  SourceCacheStats,
  SourceDetail,
  SourceHealth,
  SourceNotification,
  SourceProfileDomain,
  SourceProfileVersion,
  SourcesJobResult,
  SourcesJobStarted,
  SourcesSettings,
  SourcesSettingsUpdate,
  SourceSummary,
  TrackedSeries,
} from '../types/sources'
import { getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

const BASE = '/api/sources'
const seg = (s: string) => encodeURIComponent(s)

export const SEARCH_JOB_ID = 'sources_search'
export const seriesJobId = (source: string) => `sources_series_${source}`

// Reads
export const listSources = (f?: Fetch) => getJson<SourceSummary[]>(BASE, f)
export const getSource = (name: string, f?: Fetch) => getJson<SourceDetail>(`${BASE}/${seg(name)}`, f)
export const listTracked = (f?: Fetch) => getJson<TrackedSeries[]>(`${BASE}/tracked`, f)
export const listNotifications = (f?: Fetch) => getJson<SourceNotification[]>(`${BASE}/notifications`, f)
export const getSourcesSettings = (f?: Fetch) => getJson<SourcesSettings>(`${BASE}/settings`, f)
export const listProfiles = (f?: Fetch) => getJson<SourceProfileDomain[]>(`${BASE}/profiles`, f)
export const listAttempts = (name: string, limit = 20, f?: Fetch) =>
  getJson<SourceAttempt[]>(`${BASE}/${seg(name)}/attempts?limit=${limit}`, f)

// Jobs. `sources` is left out unless the search is limited to some sources.
export function startSearch(query: string, sources?: string[], f?: Fetch) {
  return postJson<SourcesJobStarted>(`${BASE}/search`, sources ? { query, sources } : { query }, f)
}
export const startSeries = (name: string, series_id: string, f?: Fetch) =>
  postJson<SourcesJobStarted>(`${BASE}/${seg(name)}/series`, { series_id }, f)
export const getSourcesJobResult = <R>(jobId: string, f?: Fetch) =>
  getJson<SourcesJobResult<R>>(`${BASE}/jobs/${seg(jobId)}/result`, f)

// New chapters (permission-gated; a 403 shows a banner)
export const dismissNotification = (id: number, f?: Fetch) =>
  postJson<SourceNotification>(`${BASE}/notifications/${id}/dismiss`, undefined, f)
export const untrackSeries = (source: string, series_id: string, f?: Fetch) =>
  postJson<TrackedSeries[]>(`${BASE}/tracked`, { source, series_id, tracked: false }, f)

// Source settings (PC-only)
export const setSourceEnabled = (name: string, enabled: boolean, f?: Fetch) =>
  postJson<SourceSummary>(`${BASE}/${seg(name)}/enabled`, { enabled }, pcOnlyFetch(f))
export const setAdultEnabled = (name: string, enabled: boolean, f?: Fetch) =>
  postJson<SourceSummary>(`${BASE}/${seg(name)}/adult`, { enabled }, pcOnlyFetch(f))
export const resetSourceHealth = (name: string, f?: Fetch) =>
  postJson<SourceHealth>(`${BASE}/${seg(name)}/health/reset`, undefined, pcOnlyFetch(f))
export const updateSourcesSettings = (changes: SourcesSettingsUpdate, f?: Fetch) =>
  postJson<SourcesSettings>(`${BASE}/settings`, changes, pcOnlyFetch(f))
export const clearSourcesCache = (f?: Fetch) =>
  postJson<SourceCacheStats>(`${BASE}/cache/clear`, { confirm: true }, pcOnlyFetch(f))
export const rollbackProfile = (domain: string, kind: string, version: number, f?: Fetch) =>
  postJson<SourceProfileVersion[]>(
    `${BASE}/profiles/${seg(domain)}/${seg(kind)}/rollback`, { version }, pcOnlyFetch(f),
  )
