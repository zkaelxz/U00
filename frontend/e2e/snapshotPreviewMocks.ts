import { expect, type Page } from '@playwright/test'

import { openFoldFor } from './reviewFolds'

export { clearLines, seedLines } from './resegmentLlmMocks'

/** Mocks one history snapshot of drama 3 built from the live lines: line 1 and 2 edited, line 3 absent, one extra line. */
export async function mockSnapshot(page: Page) {
  let ids: number[] = []
  const calls = { restores: 0 }
  await page.route('**/api/review/dramas/3/history', (route) =>
    route.fulfill({ json: [{ id: 7, drama_id: 3, label: 'before merge', created_at: '2026-09-29T10:00:00' }] }))
  await page.route('**/api/review/dramas/3/history/7', (route) =>
    route.fulfill({
      json: {
        id: 7, drama_id: 3, label: 'before merge', created_at: '2026-09-29T10:00:00',
        lines: [
          { id: ids[0], idx: 0, start: 0, end: 3, zh: '你好我的朋友今天天气真的很好', en: 'Old greeting', speaker: null, speaker_manual: false, dub_filename: null },
          { id: ids[1], idx: 1, start: 3, end: 6, zh: '我们一起去公园散步然后吃晚饭吧', en: 'Old walk line', speaker: null, speaker_manual: false, dub_filename: null },
          { id: 999999, idx: 2, start: 6, end: 7, zh: '旧的', en: 'Gone now', speaker: null, speaker_manual: false, dub_filename: null },
        ],
      },
    }))
  await page.route('**/api/restructure/dramas/3/history/7/restore', (route) => {
    calls.restores += 1
    return route.fulfill({ json: { history_id: 7, line_ids: ids } })
  })
  await page.goto('/#/drama/3/review')
  const rows = page.locator('.review-line:not(.review-skeleton)')
  await expect(rows).toHaveCount(3)
  ids = await rows.evaluateAll((els) => els.map((e) => Number(e.getAttribute('data-line-id'))))
  await openFoldFor(page, 'Records')
  await page.locator('summary').filter({ has: page.locator('.section-title', { hasText: /^Records$/ }) }).click()
  return calls
}

export const EXPECTED_SUMMARY = '4 lines would change (2 edited, 1 added, 1 removed).'
