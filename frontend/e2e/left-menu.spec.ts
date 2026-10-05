import { type Page } from '@playwright/test'

import { ME, mockAuth } from './authMocks'
import { expect, test } from './fixtures'

// The left rail (1024px and wider), against the seeded API (auth off), and a
// mocked member session for the permission rules.

const rail = (page: Page) => page.getByRole('navigation', { name: 'Main' })

// Every page the rail lists for the owner at the PC (Assistant needs Developer Mode, covered in assistant.spec.ts).
const ITEMS: [string, RegExp][] = [
  ['Library', /#\/library$/],
  ['Saved manga', /#\/manga$/],
  ['Library tools', /#\/library-tools$/],
  ['Sources', /#\/sources$/],
  ['Discover', /#\/discover$/],
  ['Translate text', /#\/translate$/],
  ['Live', /#\/live$/],
  ['Settings', /#\/settings$/],
  ['Admin', /#\/admin$/],
  ['Diagnostics', /#\/diagnostics$/],
  ['Benchmark Lab', /#\/benchmark$/],
]

test('the rail lists every page in groups and each item navigates and becomes current', async ({ page }) => {
  await page.goto('/#/library')
  await expect(rail(page).getByRole('heading', { name: 'Find and add' })).toBeVisible()
  await expect(rail(page).getByRole('heading', { name: 'Tools' })).toBeVisible()
  await expect(rail(page).getByRole('heading', { name: 'System' })).toBeVisible()
  // The old header nav and the cogwheel are gone on a wide screen.
  await expect(page.locator('.app-header nav')).toHaveCount(0)
  await expect(page.locator('summary[aria-label="Settings and tools"]')).toHaveCount(0)

  for (const [name, url] of ITEMS) {
    await rail(page).getByRole('link', { name, exact: true }).click()
    await expect(page).toHaveURL(url)
    await expect(rail(page).getByRole('link', { name, exact: true })).toHaveAttribute('aria-current', 'page')
    await expect(rail(page).locator('a[aria-current="page"]')).toHaveCount(1)
  }
})

test('Library stays current on a title, the reader and a comic, and the title shows under it', async ({ page }) => {
  for (const hash of ['#/drama/2/review', '#/read/2', '#/comic/2']) {
    await page.goto(`/${hash}`)
    await expect(rail(page).getByRole('link', { name: 'Library', exact: true })).toHaveAttribute('aria-current', 'page')
    await expect(rail(page).locator('a[aria-current="page"]')).toHaveCount(1)
    const row = page.getByTestId('rail-title')
    await expect(row).toBeVisible()
    await expect(row).toHaveAttribute('href', '#/drama/2')
    // One title row, and no stage links in the menu.
    await expect(rail(page).getByRole('link', { name: 'Review' })).toHaveCount(0)
  }
  await page.goto('/#/library')
  await expect(page.getByTestId('rail-title')).toHaveCount(0)
})

test('manga routes mark Saved manga current, not Library', async ({ page }) => {
  for (const hash of ['#/manga', '#/manga/mangadex/one-piece', '#/manga/mangadex/one-piece/ch1']) {
    await page.goto(`/${hash}`)
    await expect(rail(page).getByRole('link', { name: 'Saved manga', exact: true })).toHaveAttribute('aria-current', 'page')
    await expect(rail(page).getByRole('link', { name: 'Library', exact: true })).not.toHaveAttribute('aria-current', 'page')
  }
})

test('the brand links to Library', async ({ page }) => {
  await page.goto('/#/settings')
  await page.getByRole('heading', { name: 'Baihe Studio', level: 1 }).getByRole('link').click()
  await expect(page).toHaveURL(/#\/library$/)
})

test('collapsing keeps the links reachable and is remembered after a reload', async ({ page }) => {
  await page.goto('/#/library')
  const toggle = page.getByRole('button', { name: 'Side menu' })
  await expect(toggle).toHaveAttribute('aria-expanded', 'true')
  const wide = (await page.locator('.app-rail').boundingBox())!.width
  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-expanded', 'false')
  expect((await page.locator('.app-rail').boundingBox())!.width).toBeLessThan(wide / 2)
  // Labels remain the accessible names and a tooltip names each icon.
  await expect(rail(page).getByRole('link', { name: 'Sources', exact: true })).toHaveAttribute('title', 'Sources')

  await page.reload()
  await expect(page.getByRole('button', { name: 'Side menu' })).toHaveAttribute('aria-expanded', 'false')
  await rail(page).getByRole('link', { name: 'Sources', exact: true }).click()
  await expect(page).toHaveURL(/#\/sources$/)

  await page.getByRole('button', { name: 'Side menu' }).click()
  await page.reload()
  await expect(page.getByRole('button', { name: 'Side menu' })).toHaveAttribute('aria-expanded', 'true')
})

test('a wide screen at 1280 keeps the form-page column at its 1200px cap when the rail is collapsed', async ({ page }) => {
  await page.goto('/#/settings')
  await page.getByRole('button', { name: 'Side menu' }).click()
  const main = (await page.locator('.app-main').boundingBox())!
  expect(main.width).toBeLessThanOrEqual(1200)
  expect(main.width).toBeGreaterThan(1150)
  const scroll = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth])
  expect(scroll[0]).toBeLessThanOrEqual(scroll[1])
})

test('household member: no Admin; Diagnostics is listed as before (its page refuses)', async ({ page }) => {
  await mockAuth(page, { ...ME.signedIn, permissions: ['library.read', 'lines.read', 'lines.edit', 'review.use', 'jobs.start', 'jobs.cancel'] })
  await page.goto('/#/library')
  await expect(rail(page).getByRole('link', { name: 'Library', exact: true })).toBeVisible()
  await expect(rail(page).getByRole('link', { name: 'Admin', exact: true })).toHaveCount(0)
  await expect(rail(page).getByRole('link', { name: 'Assistant', exact: true })).toHaveCount(0)
  await expect(rail(page).getByRole('link', { name: 'Diagnostics', exact: true })).toBeVisible()
  await expect(rail(page).getByRole('link', { name: 'Settings', exact: true })).toBeVisible()
})
