import { expect, test } from './fixtures'
import { FOUR_LINES, seedVideo, seekVideo } from './playerMocks'

// The preview's subtitle and its "Line #" label come from the playhead time, never
// from the selected or centred line.

test.beforeEach(async ({ page }) => {
  test.skip(!seedVideo(), 'OpenCV is not installed, so there is no test video')
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.goto('/#/drama/3/review')
  await expect(page.locator('video.review-video')).toBeVisible()
})

test('with line #1 selected, a playhead inside line #2 shows line #2', async ({ page }) => {
  const first = page.locator('.review-line:not(.review-skeleton)').first()
  await first.locator('.review-idx').click()
  await expect(first).toHaveAttribute('aria-current', 'true')
  await seekVideo(page, 4.5)
  await expect(page.getByTestId('player-overlay')).toHaveText(FOUR_LINES)
  await expect(page.getByTestId('player-line')).toHaveText('Line #2')
  await seekVideo(page, 1)
  await expect(page.getByTestId('player-overlay')).toHaveText('Hello there')
  await expect(page.getByTestId('player-line')).toHaveText('Line #1')
})

test('after playing one line, seeking elsewhere shows the line at the new time', async ({ page }) => {
  const second = page.locator('.review-line:not(.review-skeleton)').nth(1)
  await second.locator('.review-idx').click()
  await second.getByRole('button', { name: '▶ Play' }).click()
  await expect(page.getByTestId('player-line')).toHaveText('Line #2')
  await page.locator('video.review-video').evaluate((v) => (v as HTMLVideoElement).pause())
  await seekVideo(page, 1)
  await expect(page.getByTestId('player-line')).toHaveText('Line #1')
  await expect(page.getByTestId('player-overlay')).toHaveText('Hello there')
})
