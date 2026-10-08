import { type Page } from '@playwright/test'

// The saved-chapters routes for seeded drama 2 (the novel). Everything else
// hits the real seeded API.
export interface MockRow {
  number: number
  title: string
  chars: number
  source: string
  imported_at: string
  unsplit: boolean
  in_translation: boolean
}

export const row = (n: number, over: Partial<MockRow> = {}): MockRow => ({
  number: n, title: `第${n}章 标题`, chars: 4000 + n, source: 'xbanxia',
  imported_at: '2026-10-08T01:02:03Z', unsplit: false, in_translation: false, ...over,
})

export function listBody(rows: MockRow[], over: Record<string, unknown> = {}) {
  return {
    drama_id: 2, present: rows.length > 0, size_bytes: 1000, split: true, total: rows.length,
    char_count: rows.reduce((a, r) => a + r.chars, 0), in_translation: rows.filter((r) => r.in_translation).length,
    translation_chars: 0, offset: 0, limit: 100, chapters: rows, ...over,
  }
}

export async function mockChapters(page: Page, body: unknown, text: (n: number, offset: number) => Record<string, unknown> = () => ({})) {
  const slices: { number: number; offset: number; limit: number }[] = []
  await page.route('**/api/novel/dramas/2/raw-novel/chapters?*', (route) => route.fulfill({ json: body }))
  await page.route('**/api/novel/dramas/2/raw-novel/chapters/*', (route) => {
    const url = new URL(route.request().url())
    const number = Number(url.pathname.split('/').pop())
    const offset = Number(url.searchParams.get('offset'))
    const limit = Number(url.searchParams.get('limit'))
    slices.push({ number, offset, limit })
    return route.fulfill({ json: text(number, offset) })
  })
  return slices
}
