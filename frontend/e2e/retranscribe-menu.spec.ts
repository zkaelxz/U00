import { expect, test, type Page } from '@playwright/test'

import { clearReviewResults, seedReviewResults } from './reviewResultsSeed'

// The line menu's "Re-transcribe…" item: it opens the line's details and
// focuses "Re-transcribe this line" without starting the job. Drama 3 has no
// audio in the seeded library, so the audio case mocks the config.

test.beforeEach(() => seedReviewResults())
test.afterAll(() => clearReviewResults())

const rows = (page: Page) => page.locator('.review-line:not(.review-skeleton)')

async function mockAudio(page: Page, audio: boolean) {
  await page.route('**/api/transcribe/dramas/3/config', (route) =>
    route.fulfill({ json: { drama_id: 3, has_audio_pipeline: audio, audio_available: audio } }))
}

async function openMenu(page: Page) {
  await page.goto('/#/drama/3/review')
  await expect(rows(page)).toHaveCount(4)
  await rows(page).nth(1).getByRole('button', { name: 'More actions for line 2' }).click()
  return page.getByRole('dialog', { name: 'Line #2' })
}

test('lands on the re-transcribe button and does not start a job', async ({ page }) => {
  await mockAudio(page, true)
  let started = false
  await page.route('**/api/transcribe/dramas/3/lines/*/retranscribe', (route) => {
    started = true
    return route.abort()
  })
  const sheet = await openMenu(page)
  await sheet.getByRole('button', { name: 'Re-transcribe…' }).click()
  await expect(sheet).toHaveCount(0)
  await expect(rows(page).nth(1).getByRole('button', { name: 'Re-transcribe this line' })).toBeFocused()
  expect(started).toBe(false)
})

test('disabled with a reason when the drama has no audio', async ({ page }) => {
  await mockAudio(page, false)
  const sheet = await openMenu(page)
  const item = sheet.getByRole('button', { name: /Re-transcribe…/ })
  await expect(item).toBeDisabled()
  await expect(item).toContainText('Needs this drama’s audio or video.')
})
