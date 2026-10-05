import { expect, test, type Page } from '@playwright/test'

// Phone project (390x844, touch): the Menu button and the left drawer that
// replace the header's nav grid and cogwheel below 1024px.

const menuButton = (page: Page) => page.getByRole('button', { name: 'Menu', exact: true })
const drawer = (page: Page) => page.getByRole('dialog', { name: 'Main menu' })

async function noSideways(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

test('the header keeps the Menu button, brand and the icon controls, with no nav grid or cogwheel', async ({ page }) => {
  await page.goto('/#/library')
  const menu = menuButton(page)
  await expect(menu).toBeVisible()
  await expect(menu).toHaveAttribute('aria-expanded', 'false')
  await expect(page.locator('.app-header').getByRole('link', { name: 'Baihe Studio' })).toHaveAttribute('href', '#/library')
  await expect(page.getByRole('button', { name: /^Jobs/ })).toBeVisible()
  await expect(page.getByRole('button', { name: /^Notifications/ })).toBeVisible()
  await expect(page.getByRole('navigation', { name: 'Main' })).toHaveCount(0)
  await expect(page.locator('summary[aria-label="Settings and tools"]')).toHaveCount(0)
  const box = (await menu.boundingBox())!
  expect(box.width).toBeGreaterThanOrEqual(44)
  expect(box.height).toBeGreaterThanOrEqual(44)
  await noSideways(page)
})

test('opening: aria-controls names the drawer, focus moves in and stays in, the page cannot scroll', async ({ page }) => {
  await page.goto('/#/library')
  const menu = menuButton(page)
  await menu.tap()
  await expect(menu).toHaveAttribute('aria-expanded', 'true')
  await expect(drawer(page)).toBeVisible()
  const id = await menu.getAttribute('aria-controls')
  await expect(page.locator(`dialog#${id}`)).toHaveAttribute('aria-label', 'Main menu')
  // Tab round and round: focus never leaves the drawer.
  for (let i = 0; i < 20; i++) {
    await page.keyboard.press('Tab')
    expect(await page.evaluate(() => !!document.activeElement?.closest('dialog.nav-drawer')), `Tab ${i}`).toBe(true)
  }
  expect(await page.evaluate(() => getComputedStyle(document.documentElement).overflow)).toBe('hidden')
  await noSideways(page)
  await page.getByRole('button', { name: 'Close menu' }).tap()
  expect(await page.evaluate(() => getComputedStyle(document.documentElement).overflow)).not.toBe('hidden')
})

test('the drawer shows the same groups as the rail, with 44px rows that all fit the width', async ({ page }) => {
  await page.goto('/#/library')
  await menuButton(page).tap()
  const nav = drawer(page).getByRole('navigation', { name: 'Main' })
  for (const heading of ['Find and add', 'Tools', 'System']) await expect(nav.getByRole('heading', { name: heading })).toBeVisible()
  const boxes = await nav.getByRole('link').evaluateAll((els) => els.map((e) => { const r = e.getBoundingClientRect(); return { h: r.height, right: r.right, left: r.left } }))
  expect(boxes.length).toBeGreaterThan(5)
  for (const b of boxes) {
    expect(b.h).toBeGreaterThanOrEqual(44)
    expect(b.left).toBeGreaterThanOrEqual(0)
    expect(b.right).toBeLessThanOrEqual(390)
  }
  const close = (await page.getByRole('button', { name: 'Close menu' }).boundingBox())!
  expect(close.width).toBeGreaterThanOrEqual(44)
  expect(close.height).toBeGreaterThanOrEqual(44)
  await expect(nav.getByRole('link', { name: 'Library', exact: true })).toHaveAttribute('aria-current', 'page')
})

test('Esc closes the drawer and focus returns to the Menu button', async ({ page }) => {
  await page.goto('/#/library')
  await menuButton(page).tap()
  await expect(drawer(page)).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(drawer(page)).toBeHidden()
  await expect(menuButton(page)).toHaveAttribute('aria-expanded', 'false')
  await expect(menuButton(page)).toBeFocused()
})

test('tapping the backdrop closes the drawer', async ({ page }) => {
  await page.goto('/#/library')
  await menuButton(page).tap()
  await expect(drawer(page)).toBeVisible()
  const { width } = (await drawer(page).boundingBox())!
  await page.touchscreen.tap(width + 20, 400)
  await expect(drawer(page)).toBeHidden()
  await expect(menuButton(page)).toBeFocused()
})

test('choosing an item navigates and closes the drawer; Benchmark Lab is two taps away', async ({ page }) => {
  await page.goto('/#/library')
  await menuButton(page).tap()
  await drawer(page).getByRole('link', { name: 'Benchmark Lab', exact: true }).tap()
  await expect(page).toHaveURL(/#\/benchmark$/)
  await expect(drawer(page)).toBeHidden()
  await expect(menuButton(page)).toHaveAttribute('aria-expanded', 'false')
  await menuButton(page).tap()
  await expect(drawer(page).getByRole('link', { name: 'Benchmark Lab', exact: true })).toHaveAttribute('aria-current', 'page')
  await noSideways(page)
})

test('tapping the page you are already on closes the drawer', async ({ page }) => {
  await page.goto('/#/library')
  await menuButton(page).tap()
  await drawer(page).getByRole('link', { name: 'Library', exact: true }).tap()
  await expect(drawer(page)).toBeHidden()
})

test('the stage strip starts right under the one-row header', async ({ page }) => {
  await page.goto('/#/drama/2/review')
  const header = (await page.locator('.app-header').boundingBox())!
  expect(header.y + header.height, 'bottom of the header (record in the PR)').toBeLessThanOrEqual(90)
  await noSideways(page)
})
