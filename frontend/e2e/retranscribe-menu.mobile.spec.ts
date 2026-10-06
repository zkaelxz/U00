import { expect, test, type Page } from '@playwright/test'

import { installHitArea } from './hitArea'
import { clearReviewResults, seedReviewResults } from './reviewResultsSeed'

// Phone project: the same menu item, as a 44px+ touch target that lands on
// the re-transcribe button without starting a job.

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
  seedReviewResults()
})
test.afterAll(() => clearReviewResults())

const rows = (page: Page) => page.locator('.review-line:not(.review-skeleton)')

test('menu item is a touch target and focuses the re-transcribe button', async ({ page }) => {
  await page.route('**/api/transcribe/dramas/3/config', (route) =>
    route.fulfill({ json: { drama_id: 3, has_audio_pipeline: true, audio_available: true } }))
  await page.goto('/#/drama/3/review')
  await expect(rows(page)).toHaveCount(4)
  await rows(page).nth(1).getByRole('button', { name: 'More actions for line 2' }).tap()
  const sheet = page.getByRole('dialog', { name: 'Line #2' })
  const item = sheet.getByRole('button', { name: 'Re-transcribe…' })
  expect(await item.evaluate((e) => window.hitHeight(e))).toBeGreaterThanOrEqual(44)
  await item.tap()
  await expect(rows(page).nth(1).getByRole('button', { name: 'Re-transcribe this line' })).toBeFocused()
})
