import { expect, test } from '@playwright/test'

import { mockManga } from './savedMangaMocks'

// Phone project (390x844, touch): the saved manga list and reader fit the
// screen. Every /api call is mocked (savedMangaMocks.ts).

async function noSideways(page: import('@playwright/test').Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

test('list, folder card and reader fit a phone', async ({ page }) => {
  const s = await mockManga(page)
  await page.goto('/#/manga')
  await expect(page.getByRole('list', { name: 'Saved series' })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Save folder' }).getByTestId('save-folder')).toBeVisible()
  await noSideways(page)

  await page.goto('/#/manga/MangaK/Test%20Camp/0001%20Chapter%201')
  await expect(page.getByTestId('comic-page-label')).toHaveText('1 / 4')
  await expect(page.getByRole('link', { name: 'Back to Test Camp' })).toBeVisible()
  await noSideways(page)
  await page.getByTestId('comic-page').nth(3).scrollIntoViewIfNeeded()
  await expect(page.getByRole('link', { name: 'Next chapter' })).toBeVisible()
  await noSideways(page)
  expect(s.unmocked).toEqual([])
})
