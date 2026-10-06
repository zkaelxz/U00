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
  const title = (await page.getByRole('heading', { name: 'Baihe Studio', level: 1 }).boundingBox())!
  expect(box.width).toBeGreaterThanOrEqual(44)
  expect(box.width).toBeLessThan(60)
  expect(box.height).toBeGreaterThanOrEqual(44)
  // Same row as the title and the Menu button; the 3 by 2 nav grid is gone, so the page starts right under it.
  expect(box.y).toBeLessThan(title.y + title.height)
  const menu = (await page.getByRole('button', { name: 'Menu', exact: true }).boundingBox())!
  expect(Math.abs(menu.y - box.y)).toBeLessThanOrEqual(2)
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
