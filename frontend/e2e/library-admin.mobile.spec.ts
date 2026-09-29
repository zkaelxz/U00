import { expect, test, type Page } from '@playwright/test'

// Phone project (390x844, touch; the file name matches /mobile\.spec\.ts/):
// Library select mode and its bottom bar. Read-only on the seeded library.

async function noSideways(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

async function heights(page: Page, selector: string) {
  return page.locator(selector).evaluateAll((els) =>
    els.filter((e) => (e as HTMLElement).offsetParent !== null)
      .map((e) => ({ h: e.getBoundingClientRect().height, text: (e.textContent ?? '').trim().slice(0, 30) })))
}

test('Library select mode: 44px checkboxes, bottom bar, no overflow', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('drama-count')).toHaveText('3 drama(s)')
  await page.getByRole('button', { name: 'Select', exact: true }).click()

  // Tapping a card toggles it; titles are not links in select mode.
  await page.locator('.drama-cards li', { hasText: 'Signal' }).click()
  await page.getByRole('checkbox', { name: 'Select Heaven Official\'s Blessing' }).check()
  const bar = page.getByRole('region', { name: 'Selection' })
  await expect(bar.getByTestId('selected-count')).toHaveText('2 selected')
  await expect(page).toHaveURL(/\/$|#\/?$/)

  for (const { h, text } of await heights(page, '.card-check')) expect(h, text).toBeGreaterThanOrEqual(44)
  for (const { h, text } of await heights(page, '.selection-bar button:not(.link), .bar-menu > summary')) expect(h, text).toBeGreaterThanOrEqual(44)
  await noSideways(page)

  // The bar is in the flow after the list (sticky only while the list scrolls
  // under it), so scrolling on always uncovers the last card.
  const last = page.locator('.drama-cards li').last()
  await last.evaluate((e) => e.scrollIntoView({ block: 'center' }))
  const lastBox = await last.boundingBox()
  const barBox = await bar.boundingBox()
  expect(lastBox && barBox && lastBox.y + lastBox.height <= barBox.y + 1).toBeTruthy()

  // Actions opens upward with 48px rows and stays on screen.
  await bar.getByText('Actions', { exact: true }).click()
  for (const { h, text } of await heights(page, '.bar-menu-body button:not(.link), .bar-menu-body select')) {
    expect(h, text).toBeGreaterThanOrEqual(48)
  }
  await expect(bar.getByRole('button', { name: 'Translate 1' })).toBeVisible()
  await noSideways(page)
  const filled = await page.locator('button.primary').evaluateAll((els) =>
    els.filter((e) => (e as HTMLElement).offsetParent !== null).length)
  expect(filled).toBeLessThanOrEqual(1)

  await bar.getByRole('button', { name: 'Done' }).click()
  await expect(page.getByRole('region', { name: 'Selection' })).toHaveCount(0)
  await expect(page.getByRole('link', { name: 'Signal', exact: true })).toBeVisible()
})
