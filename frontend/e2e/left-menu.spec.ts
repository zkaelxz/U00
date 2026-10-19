import { type Page } from '@playwright/test'

import { ME, mockAuth } from './authMocks'
import { expect, test } from './fixtures'
import { navLink, openMenu } from './settingsNav'
import { enableDeveloperMode } from './sourceHelpers'

// The left rail (1024px and wider), against the seeded API (auth off), and a
// mocked member session for the permission rules.

const rail = (page: Page) => page.getByRole('navigation', { name: 'Main' })

// Every page the rail lists for the owner at the PC with Developer Mode on (Assistant is covered in assistant.spec.ts).
const ITEMS: [string, RegExp][] = [
  ['Library', /#\/library$/],
  ['Library tools', /#\/library-tools$/],
  ['Sources', /#\/sources$/],
  ['Discover', /#\/discover$/],
  ['Quick translate', /#\/translate$/],
  ['Live', /#\/live$/],
  ['Jobs', /#\/jobs$/],
  ['Settings', /#\/settings$/],
  ['People and access', /#\/admin$/],
  ['Diagnostics', /#\/diagnostics$/],
  ['Benchmark Lab', /#\/benchmark$/],
]

test('the rail lists every page in groups and each item navigates and becomes current', async ({ page }) => {
  await enableDeveloperMode(page)
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

test('a very long title is cut off in the rail instead of scrolling it sideways', async ({ page }) => {
  await page.goto('/#/drama/2/review')
  const row = page.getByTestId('rail-title')
  await expect(row).toBeVisible()
  await row.locator('.rail-label').evaluate((el) => { el.textContent = '[Valentine\u2019s Day Q&A] '.repeat(8) })
  const rail = page.locator('.app-rail')
  const overflow = await rail.evaluate((el) => el.scrollWidth - el.clientWidth)
  expect(overflow).toBeLessThanOrEqual(0)
})

test('manga routes mark Library tools current, not Library', async ({ page }) => {
  for (const hash of ['#/manga', '#/manga/mangadex/one-piece', '#/manga/mangadex/one-piece/ch1']) {
    await page.goto(`/${hash}`)
    await expect(rail(page).getByRole('link', { name: 'Library tools', exact: true })).toHaveAttribute('aria-current', 'page')
    await expect(rail(page).getByRole('link', { name: 'Library', exact: true })).not.toHaveAttribute('aria-current', 'page')
  }
})

test('the brand links to Library', async ({ page }) => {
  await page.goto('/#/settings')
  await page.locator('.app-brand, .rail-brand').getByRole('link').click()
  await expect(page).toHaveURL(/#\/library$/)
})

test('household member: no People and access, Diagnostics or Benchmark Lab; Jobs sits under System', async ({ page }) => {
  await mockAuth(page, { ...ME.signedIn, permissions: ['library.read', 'lines.read', 'lines.edit', 'review.use', 'jobs.start', 'jobs.cancel'] })
  await page.goto('/#/library')
  await expect(rail(page).getByRole('link', { name: 'Library', exact: true })).toBeVisible()
  await expect(rail(page).getByRole('link', { name: 'People and access', exact: true })).toHaveCount(0)
  await expect(rail(page).getByRole('link', { name: 'Assistant', exact: true })).toHaveCount(0)
  await expect(rail(page).getByRole('link', { name: 'Diagnostics', exact: true })).toHaveCount(0)
  await expect(rail(page).getByRole('link', { name: 'Benchmark Lab', exact: true })).toHaveCount(0)
  await expect(rail(page).getByRole('link', { name: 'Jobs', exact: true })).toBeVisible()
  await expect(rail(page).getByRole('link', { name: 'Settings', exact: true })).toBeVisible()
})

test('the dock shows icons at rest, opens to labelled rows on hover from 900px, stays icons only to 600px, and swaps for the drawer below', async ({ page }) => {
  await page.setViewportSize({ width: 1000, height: 900 })
  await page.goto('/#/library')
  const dock = page.locator('.app-rail')
  const resting = (await dock.boundingBox())!.width
  expect(resting).toBeLessThan(90)
  // Labels stay the accessible names and a tooltip names each icon.
  await expect(rail(page).getByRole('link', { name: 'Sources', exact: true })).toHaveAttribute('title', 'Sources')
  await dock.hover()
  await expect.poll(async () => (await dock.boundingBox())!.width).toBeGreaterThan(resting * 2)
  await expect(page.getByRole('button', { name: 'Side menu' })).toHaveCount(0)

  // Opened it would cover the page's own toolbar, so a narrower window keeps the dock at its resting width.
  await page.setViewportSize({ width: 834, height: 900 })
  await page.mouse.move(600, 600)
  await dock.hover()
  await page.waitForTimeout(500)
  expect((await dock.boundingBox())!.width).toBeLessThan(90)

  await page.setViewportSize({ width: 560, height: 900 })
  await expect(page.locator('.app-rail')).toHaveCount(0)
  await expect(page.getByRole('button', { name: /^(Menu|More)$/ })).toBeVisible()
})

test('a form page at 1280 starts clear of the resting dock and does not scroll sideways', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.goto('/#/settings')
  const dock = (await page.locator('.app-rail').boundingBox())!
  const column = (await page.locator('[data-page-shell]').boundingBox())!
  expect(column.x).toBeGreaterThanOrEqual(dock.x + dock.width)
  const scroll = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth])
  expect(scroll[0]).toBeLessThanOrEqual(scroll[1])
})
