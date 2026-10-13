import type { Page } from '@playwright/test'

// Library list and series list for the sort / group specs, mocked so the shared
// seeded library is never changed. Created order (as the server sends it, newest first):
// Zeta, Mid, Early, Loose, Solo.
const base = {
  title_zh: null, author: null, studio: null, director: null, voice_actors: null, status: 'not started',
  source_language: 'ja', content_mode: null, translation_engine: null, custom_tags: [],
  created_at: null, updated_at: null,
}

export const DRAMAS = [
  { ...base, id: 101, title_en: 'Zeta', media_type: 'anime', series_id: 7, release_date: '2025-06-01' },
  { ...base, id: 102, title_en: 'Mid', media_type: 'manga', series_id: 7, release_date: '2024-03-10' },
  { ...base, id: 103, title_en: 'Early', media_type: 'anime', series_id: 8, release_date: '2023-01-02' },
  { ...base, id: 104, title_en: 'Loose', media_type: 'novel', series_id: null, release_date: null },
  { ...base, id: 105, title_en: 'Solo', media_type: 'anime', series_id: null, release_date: '2022-12-31' },
]

export const SERIES = [
  { id: 7, name: 'Saga', is_private: false, owned_by_me: true, title_count: 2, glossary_term_count: 0 },
  { id: 8, name: 'Alpha', is_private: false, owned_by_me: true, title_count: 1, glossary_term_count: 0 },
]

export async function mockLibraryOrder(page: Page) {
  await page.route('**/api/library/dramas**', (route) => {
    const url = new URL(route.request().url())
    if (url.pathname !== '/api/library/dramas') return route.fallback()
    const search = (url.searchParams.get('search') ?? '').toLowerCase()
    const items = DRAMAS.filter((d) => d.title_en.toLowerCase().includes(search))
    return route.fulfill({ json: { items, count: items.length } })
  })
  await page.route('**/api/series', (route) => route.fulfill({ json: { items: SERIES } }))
}

export async function cardTitles(page: Page) {
  return page.locator('.drama-card-title').allTextContents()
}
