// The optional web-search fallback (api/web_search_schemas.py, roadmap item 114).

export type WebSearchStatus = { enabled: boolean }

export type WebSearchConfig = { enabled: boolean; base_url: string | null }

export type WebSearchResult = { title: string; snippet: string; url: string; domain: string }

export type WebSearchResults = { query: string; source: 'searxng'; results: WebSearchResult[] }

export type WebSearchTestResult = { ok: boolean; result_count: number }
