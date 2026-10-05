import { expect, test, type Page } from '@playwright/test'

import { expectFilterAndEnter, expectHiddenItemNotListed, expectNoMatches, palette, paletteInput, searchButton } from './paletteHelpers'

// Ctrl+K quick search on a phone: the header button opens a full-width sheet.

const viaButton = (page: Page) => async () => {
  await searchButton(page).tap()
  await expect(palette(page)).toBeVisible()
}

test('the header button is 44px, opens a full-width sheet, and Enter goes to the page', async ({ page }) => {
  await page.goto('/#/library')
  const box = (await searchButton(page).boundingBox())!
  expect(box.width).toBeGreaterThanOrEqual(44)
  expect(box.height).toBeGreaterThanOrEqual(44)
  const { scroll, client } = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
  await expect(page.locator('.app-header').getByRole('link', { name: 'Baihe Studio' })).toBeVisible()

  await viaButton(page)()
  const sheet = (await palette(page).boundingBox())!
  expect(sheet.width).toBeGreaterThanOrEqual(page.viewportSize()!.width - 1)
  const row = (await palette(page).getByRole('option').first().boundingBox())!
  expect(row.height).toBeGreaterThanOrEqual(48)
  await palette(page).getByRole('button', { name: 'Close search' }).tap()
  await expect(palette(page)).toBeHidden()
  await expect(searchButton(page)).toBeFocused()
  await expectFilterAndEnter(page, viaButton(page))
})

test('shows a no-matches state', async ({ page }) => {
  await expectNoMatches(page, viaButton(page))
})

test('a page hidden through Customize menu is not listed', async ({ page }) => {
  await expectHiddenItemNotListed(page, viaButton(page))
  await expect(paletteInput(page)).toHaveCount(0)
})
