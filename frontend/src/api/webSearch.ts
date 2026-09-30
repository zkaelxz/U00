// The optional web-search fallback (api/routers/web_search_routes.py).
// status and search are library.read; the settings and the test are PC only,
// so they go through pcOnlyFetch (a 403 marks the tab remote) -- except an
// address change, which also sits behind the key-write gate: its 403 on the
// PC just means key writes are off, so it uses a plain fetch.
import type { WebSearchConfig, WebSearchResults, WebSearchStatus, WebSearchTestResult } from '../types/webSearch'
import { getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

const BASE = '/api/web-search'

export const getWebSearchStatus = (f?: Fetch) => getJson<WebSearchStatus>(`${BASE}/status`, f)

export const searchWeb = (query: string, f?: Fetch) => postJson<WebSearchResults>(`${BASE}/search`, { query }, f)

export const getWebSearchConfig = (f?: Fetch) => getJson<WebSearchConfig>(`${BASE}/config`, pcOnlyFetch(f))

export const saveWebSearchConfig = (body: { enabled?: boolean; base_url?: string; confirm?: boolean }, f?: Fetch) =>
  postJson<WebSearchConfig>(`${BASE}/config`, body, body.base_url !== undefined ? f : pcOnlyFetch(f))

export const testWebSearch = (f?: Fetch) => postJson<WebSearchTestResult>(`${BASE}/test`, {}, pcOnlyFetch(f))
