import { type Page } from '@playwright/test'

import { expect, test } from './fixtures'

// Phone chrome (390x844, touch), against the seeded API (auth off): the
// compact app header and the Reader's phone header with a long title.

async function noSideways(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

test('phone header: Report a problem is a 44px icon button on the title row', async ({ page }) => {
  await page.goto('/#/library')
  const report = page.getByRole('button', { name: 'Report a problem' })
  await expect(report).toBeVisible()
  const box = (await report.boundingBox())!
  const title = (await page.locator('.app-brand, .rail-brand').boundingBox())!
  expect(box.width).toBeGreaterThanOrEqual(44)
  expect(box.width).toBeLessThan(60)
  expect(box.height).toBeGreaterThanOrEqual(44)
  // Same row as the title; the 3 by 2 nav grid is gone, so the page starts right under it, and the bottom bar carries the navigation.
  expect(box.y).toBeLessThan(title.y + title.height)
  await expect(page.getByRole('navigation', { name: 'Quick' })).toBeVisible()
  await expect(page.getByRole('navigation', { name: 'Main' })).toHaveCount(0)
  await noSideways(page)
})

test('Reader on a phone: a long title does not push the page sideways', async ({ page }) => {
  const long = 'The Scum Villain’s Self-Saving System and Its Very Long Subtitle That Never Ends'
  await page.route('**/api/library/dramas/2', async (route) => {
    const res = await route.fetch()
    const body = await res.json()
    await route.fulfill({ response: res, json: { ...body, title_en: long } })
  })
  await page.goto('/#/read/2')
  await expect(page.locator('.reader-title')).toHaveText(long)
  await noSideways(page)
  const aa = (await page.getByRole('button', { name: /Aa|Reading settings/ }).first().boundingBox())!
  expect(aa.x + aa.width).toBeLessThanOrEqual(390)
})

test('Library at 360px: the title and the Titles/Lines toggle never break inside a word', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 })
  await page.goto('/#/library')
  await expect(page.locator('.page-title')).toHaveText('Library')
  await expect(page.locator('.segmented span')).toHaveText(['Titles', 'Lines'])
  // Rendered lines of each one-word label, counted from the text's own boxes.
  const lines = await page.locator('.page-title, .segmented span').evaluateAll((els) =>
    els.map((el) => {
      const range = document.createRange()
      range.selectNodeContents(el)
      return new Set([...range.getClientRects()].map((r) => Math.round(r.top))).size
    }))
  expect(lines).toEqual([1, 1, 1])
  await noSideways(page)
})
