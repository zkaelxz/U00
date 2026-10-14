import { expect, type Page } from '@playwright/test'

import { openDetailsTab } from './source-groups'

// Merge or move chapters on the Details tab of the seeded novel (drama 2).
// The three restructure routes are mocked, so the seeded library never changes.

const INFO = {
  drama_id: 2,
  kind: 'novel',
  chapters: [
    { id: '1', title: 'Chapter 1', pages: null, chars: 1200 },
    { id: '2', title: 'Chapter 2', pages: null, chars: 3400 },
  ],
  targets: [{ id: 5, title: 'Heaven Official (part 2)' }],
}

export async function mockRestructure(page: Page) {
  const calls: { path: string; body: Record<string, unknown> | null }[] = []
  await page.route('**/api/dramas/2/restructure', (r) => {
    if (r.request().method() === 'GET') return r.fulfill({ json: INFO })
    calls.push({ path: 'apply', body: r.request().postDataJSON() })
    return r.fulfill({
      json: {
        source_id: 2, target_id: 5, kind: 'move', moved_chapters: 1, moved_pages: 0,
        skipped_duplicates: 0, source_empty: false, snapshot: '20261014-020000-000000001-move-2-to-5',
        warnings: [],
      },
    })
  })
  await page.route('**/api/dramas/2/restructure/preview', (r) => {
    const body = r.request().postDataJSON() as Record<string, unknown>
    calls.push({ path: 'preview', body })
    return r.fulfill({
      json: {
        source_id: 2, target_id: 5, kind: body.kind, media: 'novel', target_title: 'Heaven Official (part 2)',
        chapters: [{ ...INFO.chapters[1], duplicate: false, moves: true, in_translation: true }],
        moving_chapters: 1, moving_pages: 0, skipped_duplicates: 0, source_empty_after: false,
        stays: ["1 chapter(s) were already copied into this title's translation text; that copy stays here."],
        notes: [], blocked: [], fingerprint: 'f'.repeat(64),
      },
    })
  })
  return calls
}

export async function openRestructure(page: Page) {
  await page.goto('/#/drama/2/source')
  await openDetailsTab(page)
  await page.locator('.section-title', { hasText: 'Merge or move chapters' }).click()
  await expect(page.getByTestId('title-restructure')).toBeVisible()
}
