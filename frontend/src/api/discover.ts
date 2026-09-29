// Discover page: the known-titles catalogue (api/routers/discover_routes.py)
// and its lookup helpers (api/routers/discover_lookup_routes.py).
//
// Reads are `library.read`; add/seed/import/bulk-commit are `admin.library`;
// the URL-reading helpers are `media.import_url`; delete is PC-only, so it
// goes through pcOnlyFetch. `engine` is only a name: keys stay on the PC.
import type {
  BaihehubResult,
  BulkCommitResult,
  BulkEntry,
  BulkExtractResult,
  DiscoverJobStarted,
  ImportedDrama,
  ImportSuggestion,
  KnownTitle,
  KnownTitleCreate,
  KnownTitleList,
  NavigationHelpResult,
  Platform,
  SearchLink,
  TitleFilters,
  TranslateQueryResult,
} from '../types/discover'
import type { SourcesJobResult } from '../types/sources'
import { getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

const BASE = '/api/discover'

export const BULK_JOB_ID = 'discover_bulk_extract'
export const NAV_JOB_ID = 'discover_navigation_help'

// Only the filters that are set go in the query string.
export function queryString(params: Record<string, string | undefined>): string {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v) q.set(k, v)
  const s = q.toString()
  return s ? `?${s}` : ''
}

// An omitted engine means the server default (Claude, paid).
const withEngine = <T extends object>(body: T, engine?: string) => (engine ? { ...body, engine } : body)

// Catalogue
export const listTitles = (filters: TitleFilters = {}, f?: Fetch) =>
  getJson<KnownTitleList>(`${BASE}/titles${queryString({ ...filters })}`, f)
export const createTitle = (body: KnownTitleCreate, f?: Fetch) => postJson<KnownTitle>(`${BASE}/titles`, body, f)
export const seedTitles = (f?: Fetch) => postJson<{ added: number; total: number }>(`${BASE}/titles/seed`, undefined, f)
export const deleteTitle = (id: number, f?: Fetch) =>
  postJson<{ deleted: boolean; id: number }>(`${BASE}/titles/${id}/delete`, { confirm: true }, pcOnlyFetch(f))
export const importToLibrary = (id: number, f?: Fetch) =>
  postJson<ImportedDrama>(`${BASE}/titles/${id}/import-to-library`, undefined, f)

// Platforms and search links (built on the server, no request made)
export const listPlatforms = (language = '', content_type = '', f?: Fetch) =>
  getJson<{ platforms: Platform[] }>(`${BASE}/platforms${queryString({ language, content_type })}`, f).then(
    (r) => r.platforms,
  )
export const searchLinks = (q: string, format = '', f?: Fetch) =>
  getJson<{ links: SearchLink[] }>(`${BASE}/search-links${queryString({ q, format })}`, f).then((r) => r.links)

// Lookup helpers
export const translateQuery = (q: string, engine?: string, f?: Fetch) =>
  postJson<TranslateQueryResult>(`${BASE}/translate-query`, withEngine({ q }, engine), f)
export const baihehubSearch = (q: string, f?: Fetch) => postJson<BaihehubResult>(`${BASE}/baihehub-search`, { q }, f)
export const importSuggestion = (url: string, engine?: string, f?: Fetch) =>
  postJson<ImportSuggestion>(`${BASE}/import-suggestion`, withEngine({ url }, engine), f)

// Jobs (one fixed id each; the result lives in this API process only)
export const startBulkExtract = (urls: string[], source_label: string, engine?: string, f?: Fetch) =>
  postJson<DiscoverJobStarted>(`${BASE}/bulk-extract`, withEngine({ urls, source_label }, engine), f)
export const getBulkExtractResult = (f?: Fetch) =>
  getJson<SourcesJobResult<BulkExtractResult>>(`${BASE}/bulk-extract/result`, f)
export const bulkCommit = (entries: BulkEntry[], source_label: string, f?: Fetch) =>
  postJson<BulkCommitResult>(`${BASE}/bulk-commit`, { entries, source_label }, f)
export const startNavigationHelp = (
  body: { url: string; goal: string; target_language: string },
  engine?: string,
  f?: Fetch,
) => postJson<DiscoverJobStarted>(`${BASE}/navigation-help`, withEngine(body, engine), f)
export const getNavigationHelpResult = (f?: Fetch) =>
  getJson<SourcesJobResult<NavigationHelpResult>>(`${BASE}/navigation-help/result`, f)
