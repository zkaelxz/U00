import { expect, test, type Page } from '@playwright/test'
import { openFillIn, openGroup } from './source-groups'

// Phone (390x844): Edit details' credits and cover, the known platforms list and the EPUB
// chapter range fit the width.

async function expectNoHorizontalOverflow(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

test('Source preamble panels fit a phone', async ({ page }) => {
  await page.goto('/#/drama/1/source')
  await openGroup(page, 'Details and credits')
  await page.locator('.section-title', { hasText: 'Edit details' }).click()
  await expect(page.getByRole('button', { name: 'Romanize credits' })).toBeVisible()
  await openFillIn(page, 'From a page or text')
  await page.getByText('Known official platforms', { exact: true }).click()
  await expect(page.getByRole('list', { name: 'Known official platforms' })).toBeVisible()
  const link = page.getByRole('list', { name: 'Known official platforms' }).getByRole('link').first()
  expect((await link.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44)
  await expectNoHorizontalOverflow(page)
})
