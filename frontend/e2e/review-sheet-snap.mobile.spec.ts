import { expect, test } from '@playwright/test'

// Phone project: a timing-flagged line's "⋯" sheet carries the desktop toolbar's Snap to speech, with the
// suggested times as visible text. Lines and the timing endpoints are mocked and every other write is aborted.

const line = (id: number, flagged: boolean) => ({
  id, idx: id - 1, start: id * 10, end: id * 10 + 4, zh: `句${id}`, en: `Line ${id}`, speaker: null,
  speaker_manual: false, sfx: false, flag: flagged ? 'timing_drift' : null,
  flag_note: flagged ? 'Starts 2.0 s before speech.' : null, dub_filename: null, lang: null,
})

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

test('the line sheet offers Snap to speech with its times', async ({ page }) => {
  const snaps: unknown[] = []
  await page.route('**/api/**', (route) => (route.request().method() === 'GET' ? route.continue() : route.abort()))
  await page.route('**/api/review/dramas/3/lines?*', (route) =>
    route.fulfill({ json: { lines: [line(1, false), line(2, true)], page: 1, page_size: 40, total: 2, flagged_count: 1, untranslated_count: 0 } }))
  await page.route('**/api/timing-check/dramas/3', (route) =>
    route.fulfill({
      json: {
        job_id: '', status: 'idle', progress: 0, message: '', result: null,
        last_check: { checked_at: '2026-10-09T10:00:00', flagged: 1, notice: null },
        suggestions: [{ line_id: 2, start: 20, end: 24, new_start: 21.9, new_end: 24 }],
      },
    }))
  await page.route('**/api/timing-check/dramas/3/snap', (route) => {
    snaps.push(route.request().postDataJSON())
    return route.fulfill({ json: { snapped: 1, stale_ids: [], history_id: 9 } })
  })
  await page.goto('/#/drama/3/review')
  await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(2)

  await page.locator('.review-line[data-line-id="1"]').getByRole('button', { name: 'More actions for line 1' }).tap()
  await expect(page.getByRole('button', { name: 'Edit details' })).toBeVisible()
  await expect(page.getByRole('button', { name: /Snap to speech/ })).toHaveCount(0)
  await page.keyboard.press('Escape')

  await page.locator('.review-line[data-line-id="2"]').getByRole('button', { name: 'More actions for line 2' }).tap()
  const snap = page.getByRole('button', { name: /Snap to speech/ })
  await expect(snap).toContainText('saves a history snapshot first')
  await snap.tap()
  await expect.poll(() => snaps).toEqual([{ line_ids: [2] }])
})
