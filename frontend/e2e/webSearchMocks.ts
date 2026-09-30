import type { Page } from '@playwright/test'

import { searchResult } from './sourcesMocks'

// Web-search fallback (item 114): /api/web-search is mocked (no SearXNG).

export const WEB_RESULTS = {
  query: 'Nowhere Title',
  source: 'searxng',
  results: [
    { title: 'Nowhere Title – a fan wiki', snippet: 'Episode list and cast for Nowhere Title.', url: 'https://wiki.example/nowhere', domain: 'wiki.example' },
    { title: 'Nowhere Title novel page', snippet: '', url: 'https://novels.example/book/42', domain: 'novels.example' },
  ],
}

export const emptySearch = () => ({ ...searchResult(0), query: 'Nowhere Title', errors: {} })

export async function mockWebSearch(page: Page, enabled: boolean) {
  const searches: unknown[] = []
  await page.route('**/api/web-search/status', (route) => route.fulfill({ json: { enabled } }))
  await page.route('**/api/web-search/search', (route) => {
    searches.push(route.request().postDataJSON())
    return route.fulfill({ json: WEB_RESULTS })
  })
  return { searches }
}
