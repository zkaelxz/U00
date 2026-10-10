import { expect, test } from '@playwright/test'

import { installHitArea } from './hitArea'
import { mockMakeSubtitles } from './makeSubtitlesMocks'

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

const noSidewaysScroll = async (page: import('@playwright/test').Page) => {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

test('the card is one column with 44px controls and a full-width primary', async ({ page }) => {
  await mockMakeSubtitles(page)
  await page.setViewportSize({ width: 360, height: 780 })
  await page.goto('/')
  const c = page.getByRole('region', { name: 'Make subtitles' })
  await expect(c).toBeVisible()
  await noSidewaysScroll(page)
  const primary = c.getByRole('button', { name: 'Make subtitles' })
  for (const el of [primary, c.getByLabel('Source language'), c.getByLabel('Translator'), c.getByRole('button', { name: 'Choose a file' })]) {
    const h = await el.evaluate((n) => window.hitHeight(n))
    expect(h).toBeGreaterThanOrEqual(44)
  }
  const box = (await primary.boundingBox())!
  const cardBox = (await c.boundingBox())!
  expect(box.width).toBeGreaterThan(cardBox.width * 0.8)

  await c.getByLabel('Audio or video file').setInputFiles({ name: 'a.mp3', mimeType: 'audio/mpeg', buffer: Buffer.from('abc') })
  await primary.click()
  await expect(c.getByRole('button', { name: 'Download SRT' })).toBeVisible()
  await noSidewaysScroll(page)
})
