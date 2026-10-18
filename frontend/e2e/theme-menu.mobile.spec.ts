import { expect, test, type Page } from '@playwright/test'

// Phone (390x844, touch): the theme button sits in the More menu,
// is a 44px target, and its menu fits the screen with no sideways scroll.

async function noSideways(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

const button = (page: Page) => page.getByRole('button', { name: /^Theme: / })

test.afterEach(async ({ page }) => {
  await page.evaluate(() => localStorage.removeItem('baihe.theme'))
})

test('phone menu: theme button is 44px and picking sepia works', async ({ page }) => {
  await page.goto('/#/library')
  await page.getByRole('button', { name: 'More', exact: true }).tap()
  const btn = button(page)
  await expect(btn).toBeVisible()
  const box = (await btn.boundingBox())!
  expect(box.width).toBeGreaterThanOrEqual(44)
  expect(box.height).toBeGreaterThanOrEqual(44)
  await noSideways(page)

  await btn.tap()
  const menu = page.getByRole('menu', { name: 'Theme' })
  await expect(menu).toBeVisible()
  const mbox = (await menu.boundingBox())!
  expect(mbox.x).toBeGreaterThanOrEqual(0)
  expect(mbox.x + mbox.width).toBeLessThanOrEqual(390)
  for (const item of await page.getByRole('menuitemradio').all()) {
    expect((await item.boundingBox())!.height).toBeGreaterThanOrEqual(44)
  }
  await noSideways(page)

  await page.getByRole('menuitemradio', { name: 'Sepia', exact: true }).tap()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'sepia')
  expect(await page.evaluate(() => getComputedStyle(document.body).backgroundColor)).toBe('rgb(239, 228, 203)')
  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'sepia')
  await noSideways(page)
})

test('phone: OLED black is true black, persists and does not scroll sideways', async ({ page }) => {
  await page.goto('/#/library')
  await page.getByRole('button', { name: 'More', exact: true }).tap()
  await button(page).tap()
  await page.getByRole('menuitemradio', { name: 'OLED black', exact: true }).tap()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'oled')
  expect(await page.evaluate(() => getComputedStyle(document.body).backgroundColor)).toBe('rgb(0, 0, 0)')
  await page.waitForFunction(() => localStorage.getItem('baihe.theme') === 'oled')
  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'oled')
  await noSideways(page)
})
