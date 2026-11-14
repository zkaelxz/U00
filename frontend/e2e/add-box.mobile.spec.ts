import { expect, test } from '@playwright/test'

import { VIDEO_LINK, mockAddLink, probeOf } from './addBoxMocks'
import { installHitArea } from './hitArea'

// Phone: the Library keeps its first title card on the first screen, with one Add button that opens
// the Add page; the box there fits 390 and 320 without sideways scroll.

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

const noSideways = async (page: import('@playwright/test').Page) => {
  const { scroll, client } = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

for (const width of [390, 320]) {
  test(`at ${width}: one Add button on the Library, the box on the Add page, and back with New title`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 })
    await mockAddLink(page, probeOf({ title: '【直播】第一回 a rather long stream title that has to wrap', platform: 'YouTube', channel: 'Some Channel' }))
    await page.goto('/')
    await expect(page.getByTestId('drama-count')).toBeVisible()
    await expect(page.getByRole('region', { name: 'Add' })).toHaveCount(0)
    const add = page.locator('.page-head').getByRole('link', { name: 'Add', exact: true })
    expect(await add.evaluate((n) => window.hitHeight(n))).toBeGreaterThanOrEqual(44)
    const card = (await page.locator('.drama-card').first().boundingBox())!
    expect(card.y, 'first title card starts on the first screen').toBeLessThan(844)
    await noSideways(page)

    await add.tap()
    await expect(page).toHaveURL(/#\/make-subtitles$/)
    const b = page.getByRole('region', { name: 'Add' })
    await noSideways(page)
    await b.getByRole('textbox', { name: 'Paste a link' }).fill(VIDEO_LINK)
    await expect(b.getByTestId('add-found')).toContainText('a rather long stream title')
    await noSideways(page)
    for (const name of ['Make English subtitles', "Only add it, don't start", 'Choose a file']) {
      expect(await b.getByRole('button', { name }).evaluate((n) => window.hitHeight(n))).toBeGreaterThanOrEqual(44)
    }
    await b.getByRole('button', { name: "Only add it, don't start" }).tap()
    await expect(page.getByRole('dialog', { name: 'New title' }).getByLabel(/^Link/)).toHaveValue(VIDEO_LINK)
  })
}
