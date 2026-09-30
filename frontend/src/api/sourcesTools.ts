// Sources tools and the Discover pasted listing (api/routers/sources_tools_routes.py).
//
//   POST /api/sources/url/preflight        {url}                 -> {job_id: 'sources_url_preflight'}
//   POST /api/sources/url/preview-pasted   {url, html}           -> PastedPreview (parse only)
//   POST /api/sources/url/import-pasted    {url, html, drama_id} -> {job_id: 'sourceimport_<drama>'}
//   POST /api/sources/url/identify-media   {url, html?}          -> {job_id: 'sources_url_identify'}
//   GET  /api/sources/url/identify-media/resource?run_id&index   -> full URL (PC only)
//   GET  /api/sources/url/extractions?limit                      -> SourceExtraction[] (admin)
//   POST /api/discover/bulk-extract/pasted {text, source_label, engine?} -> {job_id: 'discover_bulk_extract'}
//
// Job results: GET /api/sources/jobs/{id}/result (useSourcesJob); the pasted
// listing shares the bulk-extract job and its result route. Pasted text is
// sent, parsed on the server and never shown back.
import type { DiscoverJobStarted } from '../types/discover'
import type { SourcesJobStarted } from '../types/sources'
import type { MediaResourceUrl, PastedPreview, SourceExtraction } from '../types/sourcesTools'
import { getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

export const PREFLIGHT_JOB_ID = 'sources_url_preflight'
export const IDENTIFY_JOB_ID = 'sources_url_identify'
// The server's cap on pasted page source (bytes of UTF-8).
export const MAX_PASTED_HTML_BYTES = 5_000_000
// The server's cap on pasted listing text (characters).
export const MAX_PASTED_LISTING_CHARS = 200_000

export const startPreflight = (url: string, f?: Fetch) =>
  postJson<SourcesJobStarted>('/api/sources/url/preflight', { url }, f)

export const previewPasted = (url: string, html: string, f?: Fetch) =>
  postJson<PastedPreview>('/api/sources/url/preview-pasted', { url, html }, f)

export const startPastedImport = (url: string, html: string, drama_id: number, f?: Fetch) =>
  postJson<SourcesJobStarted>('/api/sources/url/import-pasted', { url, html, drama_id }, f)

export const startIdentifyMedia = (url: string, html?: string | null, f?: Fetch) =>
  postJson<SourcesJobStarted>('/api/sources/url/identify-media', html ? { url, html } : { url }, f)

export const getIdentifiedResource = (run_id: string, index: number, f?: Fetch) =>
  getJson<MediaResourceUrl>(
    `/api/sources/url/identify-media/resource?run_id=${encodeURIComponent(run_id)}&index=${index}`,
    pcOnlyFetch(f),
  )

export const listExtractions = (limit = 15, f?: Fetch) =>
  getJson<SourceExtraction[]>(`/api/sources/url/extractions?limit=${limit}`, f)

export const startBulkPasted = (text: string, source_label: string, engine?: string, f?: Fetch) =>
  postJson<DiscoverJobStarted>(
    '/api/discover/bulk-extract/pasted',
    engine ? { text, source_label, engine } : { text, source_label },
    f,
  )

/** UTF-8 size of pasted text, to refuse over-cap pastes before sending. */
export const utf8Bytes = (s: string) => new TextEncoder().encode(s).length
