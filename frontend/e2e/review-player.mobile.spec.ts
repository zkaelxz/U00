import { expect, test } from '@playwright/test'

import { installHitArea } from './hitArea'
import { FOUR_LINES, seedVideo, seekVideo, stubFullscreen } from './playerMocks'

// Phone: the overlay fits the small picture, stays readable and the new button is a real touch target.

test.beforeEach(async ({ page }) => {
  seedVideo()
  await installHitArea(page)
  await stubFullscreen(page)
  await page.goto('/#/drama/3/review')
  await expect(page.locator('video.review-video')).toBeVisible()
})

test('phone: a four-line subtitle stays inside the picture and the page does not scroll sideways', async ({ page }) => {
  const video = page.locator('video.review-video')
  const before = await video.boundingBox()
  await seekVideo(page, 4)
  const overlay = page.getByTestId('player-overlay')
  await expect(overlay).toHaveText(FOUR_LINES)
  const v = (await video.boundingBox())!
  expect(v).toEqual(before)
  const o = (await overlay.boundingBox())!
  expect(o.x).toBeGreaterThanOrEqual(v.x)
  expect(o.x + o.width).toBeLessThanOrEqual(v.x + v.width)
  expect(o.y).toBeGreaterThanOrEqual(v.y)
  expect(o.y + o.height).toBeLessThanOrEqual(v.y + v.height)
  expect(await overlay.evaluate((el) => parseFloat(getComputedStyle(el).fontSize))).toBeGreaterThanOrEqual(14)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})

test('phone: the Full screen button is a 44px target and works', async ({ page }) => {
  const button = page.getByRole('button', { name: 'Full screen' })
  await button.scrollIntoViewIfNeeded()
  expect(await button.evaluate((el) => el.getBoundingClientRect().width)).toBeGreaterThanOrEqual(44)
  expect(await button.evaluate((el) => window.hitHeight(el))).toBeGreaterThanOrEqual(44)
  await button.tap()
  await expect(page.getByRole('button', { name: 'Exit full screen' })).toHaveAttribute('aria-pressed', 'true')
  expect(await page.evaluate(() => (window as unknown as { __fsRequests: string[] }).__fsRequests)).toEqual(['player-frame'])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})
