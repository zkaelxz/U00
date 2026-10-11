import { expect, test, type Page } from '@playwright/test'

// The top-right toolbar (search, jobs, bell, report, theme) is part of the sticky app header and must stay in
// view on a long page without covering the workspace strip or Review's own sticky toolbar.

async function scrollFar(page: Page, tallSelector: string) {
  await page.evaluate((sel) => { (document.querySelector(sel) as HTMLElement).style.minHeight = '4000px' }, tallSelector)
  await page.evaluate(() => window.scrollTo(0, 1500))
  expect(await page.evaluate(() => window.scrollY)).toBeGreaterThan(500)
}

const noSideways = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)

test('the toolbar stays at the top of a long page and its buttons stay clickable', async ({ page }) => {
  await page.goto('/#/library')
  await expect(page.getByTestId('stats')).toBeVisible()
  await scrollFar(page, '.app-main')
  const header = page.locator('.app-header')
  await expect(header).toBeInViewport({ ratio: 1 })
  expect((await header.boundingBox())!.y).toBeLessThanOrEqual(1)
  await expect(page.getByRole('button', { name: 'Report a problem' })).toBeInViewport({ ratio: 1 })
  const barH = await page.evaluate(() => parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--app-header-h')))
  expect(barH).toBeCloseTo((await header.boundingBox())!.height, 0)
})

test('the workspace strip and Review toolbar sit under the header, not behind it', async ({ page }) => {
  await page.goto('/#/drama/2/review')
  await expect(page.getByTestId('drama-title')).toBeVisible()
  await scrollFar(page, 'section.workspace')
  const header = (await page.locator('.app-header').boundingBox())!
  const strip = (await page.locator('.ws-strip').boundingBox())!
  expect(strip.y, 'strip starts at the header bottom').toBeGreaterThanOrEqual(header.y + header.height - 1)
  expect(strip.y).toBeLessThanOrEqual(header.y + header.height + 1)
  const toolbar = page.locator('.review-toolbar')
  if (await toolbar.count()) {
    const t = (await toolbar.boundingBox())!
    expect(t.y, 'Review toolbar clears the strip').toBeGreaterThanOrEqual(strip.y + strip.height - 1)
  }
  const watch = page.locator('.review-watch')
  if (await watch.count()) {
    const w = (await watch.boundingBox())!
    expect(w.y, 'sticky review card clears the header').toBeGreaterThanOrEqual(header.y + header.height - 1)
  }
  expect(await noSideways(page)).toBe(true)
})
