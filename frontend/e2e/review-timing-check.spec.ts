import { expect, test, type Page } from '@playwright/test'

// Review shows a timing_drift flag with its note and a "Snap to speech" action,
// "Check timing" starts the job, and "Snap all flagged" sends the snap request.
// Lines and the timing endpoints are mocked and every other write is aborted,
// so the shared library is never touched.

const NOTE = 'Starts 2.0 s before speech.'
const line = (id: number, flagged: boolean) => ({
  id, idx: id - 1, start: id * 10, end: id * 10 + 4, zh: `句${id}`, en: `Line ${id}`, speaker: null,
  speaker_manual: false, sfx: false, flag: flagged ? 'timing_drift' : null,
  flag_note: flagged ? NOTE : null, dub_filename: null, lang: null,
})
const SUGGESTION = { line_id: 2, start: 20, end: 24, new_start: 21.9, new_end: 24 }

async function open(page: Page, seen: { snaps: unknown[]; runs: number }) {
  const lines = [line(1, false), line(2, true), line(3, false)]
  await page.route('**/api/**', (route) => (route.request().method() === 'GET' ? route.continue() : route.abort()))
  await page.route('**/api/review/dramas/3/lines?*', (route) =>
    route.fulfill({ json: { lines, page: 1, page_size: 40, total: 3, flagged_count: 1, untranslated_count: 0 } }))
  await page.route('**/api/timing-check/dramas/3', (route) =>
    route.fulfill({
      json: {
        job_id: '', status: 'idle', progress: 0, message: '', result: null,
        last_check: { checked_at: '2026-10-09T10:00:00', flagged: 1, notice: null },
        suggestions: [SUGGESTION],
      },
    }))
  await page.route('**/api/timing-check/dramas/3/run', (route) => {
    seen.runs += 1
    return route.fulfill({ json: { job_id: 'timingchk_3' } })
  })
  await page.route('**/api/timing-check/dramas/3/snap', (route) => {
    seen.snaps.push(route.request().postDataJSON())
    return route.fulfill({ json: { snapped: 1, stale_ids: [], history_id: 9 } })
  })
  await page.goto('/#/drama/3/review')
  await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(3)
}

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

test('the flagged line shows its note and snaps to speech', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  const seen = { snaps: [] as unknown[], runs: 0 }
  await open(page, seen)
  const row = page.locator('.review-line[data-line-id="2"]')
  await expect(row.getByTestId('line-flag')).toContainText('Starts 2.0 s before speech')
  await expect(page.locator('.review-line[data-line-id="1"]').getByRole('button', { name: 'Snap to speech' })).toHaveCount(0)
  await row.getByRole('button', { name: 'Snap to speech' }).click()
  await expect.poll(() => seen.snaps).toEqual([{ line_ids: [2] }])
})

test('Check timing starts the job and Snap all flagged sends no line ids', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  const seen = { snaps: [] as unknown[], runs: 0 }
  await open(page, seen)
  const fold = page.locator('details.section').filter({ has: page.locator(':scope > summary .section-title', { hasText: /^Flag lines for review$/ }) })
  if ((await fold.getAttribute('open')) === null) await fold.locator(':scope > summary').click()
  const panel = fold.getByTestId('timing-check')
  await expect(panel.getByTestId('timing-summary')).toHaveText('1 line flagged for timing.')
  await expect(panel.getByRole('button', { name: 'Snap all flagged (1)' })).toBeEnabled()
  await panel.getByRole('button', { name: 'Snap all flagged (1)' }).click()
  await expect(panel.getByTestId('timing-snap-note')).toContainText('Snapped 1 line to speech.')
  expect(seen.snaps).toEqual([{}])
  await panel.getByRole('button', { name: 'Check timing' }).click()
  await expect.poll(() => seen.runs).toBe(1)
})
