import type { Page } from '@playwright/test'

import { clearCheckLines, seedCheckLines } from './compareSelectedHelpers'
import { expect, test } from './fixtures'

// Review reopens on the line the person was last on, per title, kept in this browser. Title 3 gets 100 lines (three
// pages); three of them need a look (#24, #7 and #12, in that order).

test.afterAll(() => clearCheckLines(3))

const seedPlaceLines = () => seedCheckLines({ count: 100 })
const PLACE_KEY = 'baihe.pref.review.at.3'
const row = (page: Page, n: number) => page.locator('.review-line').filter({ has: page.locator('.review-idx', { hasText: new RegExp(`^#${n}$`) }) })

const realRow = (page: Page) => page.locator('.review-line:not(.review-skeleton)').first()
const status = (page: Page) => page.locator('.review-status')
const pageNumber = (page: Page) => page.getByLabel('Page number').first()
const kept = (page: Page) => page.evaluate((k) => localStorage.getItem(k), PLACE_KEY)
const queueRadio = (page: Page) => page.getByRole('group', { name: 'Lines to show' }).getByRole('radio', { name: /^Need a look/ })
const allRadio = (page: Page) => page.getByRole('group', { name: 'Lines to show' }).getByRole('radio', { name: /^All/ })

async function leave(page: Page) {
  await page.goto('/#/library')
  await expect(page.locator('.review-line')).toHaveCount(0)
}

// The list's own pages (40 lines each), by page number, from here on.
function countPages(page: Page): Map<number, number> {
  const seen = new Map<number, number>()
  page.on('request', (req) => {
    const url = new URL(req.url())
    if (!/\/api\/review\/dramas\/3\/lines$/.test(url.pathname) || url.searchParams.get('page_size') !== '40') return
    const n = Number(url.searchParams.get('page'))
    seen.set(n, (seen.get(n) ?? 0) + 1)
  })
  return seen
}

async function expectInView(page: Page, n: number) {
  const box = await row(page, n).boundingBox()
  const height = page.viewportSize()!.height
  expect(box, `#${n} is drawn`).not.toBeNull()
  expect(box!.y, `#${n} is below the window's top`).toBeGreaterThanOrEqual(0)
  expect(box!.y + box!.height, `#${n} is above the window's bottom`).toBeLessThanOrEqual(height)
  // Brought clear of the bottom edge, not just into view.
  expect(box!.y, `#${n} sits in the upper half`).toBeLessThan(height / 2)
}

async function keepLine55(page: Page) {
  await page.goto('/#/drama/3/review')
  await expect(realRow(page)).toBeVisible()
  await page.getByRole('button', { name: 'Next page' }).first().click()
  await expect(row(page, 41)).toBeVisible()
  await row(page, 55).locator('.review-idx').click()
  await expect(row(page, 55)).toHaveAttribute('aria-current', 'true')
  await expect.poll(() => kept(page)).toContain('"lineNumber":55')
}

test('Full view reopens Review on the page and line it was left on, without loading page 1 first', async ({ page }) => {
  seedPlaceLines()
  await keepLine55(page)
  await leave(page)
  const pages = countPages(page)
  await page.goto('/#/drama/3/review')
  await expect(row(page, 55)).toHaveAttribute('aria-current', 'true')
  await expect(pageNumber(page)).toHaveValue('2')
  await expect(row(page, 55)).toBeFocused()
  await expect(status(page)).toHaveText('Back where you stopped, on line 55.')
  await expectInView(page, 55)
  expect(pages.get(2) ?? 0).toBeGreaterThan(0)
  expect(pages.get(1) ?? 0, 'requests for page 1').toBe(0)
})

test('a link that names lines wins over the kept place and leaves it as it is', async ({ page }) => {
  seedPlaceLines()
  await keepLine55(page)
  const before = await kept(page)
  await leave(page)
  await page.goto('/#/drama/3/review?focus=flagged')
  await expect(page.getByRole('radio', { name: /^Flagged/ })).toBeChecked()
  await expect(row(page, 12)).toHaveAttribute('aria-current', 'true')
  await expect(page.locator('.review-line')).toHaveCount(2)
  await expect(status(page)).toHaveText('')
  expect(await kept(page)).toBe(before)
  // ?focus=all names every line: All opens on its first page as it always has.
  await leave(page)
  await page.goto('/#/drama/3/review?focus=all')
  await expect(row(page, 12)).toHaveAttribute('aria-current', 'true')
  await expect(pageNumber(page)).toHaveValue('1')
  await expect(status(page)).toHaveText('')
  expect(await kept(page)).toBe(before)
})

test('a kept line whose id is gone reopens on the line that has its number now, with no error', async ({ page }) => {
  seedPlaceLines()
  await keepLine55(page)
  await leave(page)
  // New ids for the same 100 lines, as when a title is transcribed again.
  seedPlaceLines()
  await page.goto('/#/drama/3/review')
  await expect(row(page, 55)).toHaveAttribute('aria-current', 'true')
  await expect(pageNumber(page)).toHaveValue('2')
  await expect(status(page)).toHaveText('Back where you stopped, on line 55.')
  await expect(page.getByRole('alert')).toHaveCount(0)
})

test.describe('Simple', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('baihe.pref.title.view', '"simple"'))
  })

  test('a line kept under All waits for All: Need a look opens on its first card, and All on the kept line', async ({ page }) => {
    seedPlaceLines()
    await page.goto('/#/drama/3')
    await expect(queueRadio(page)).toBeChecked()
    await allRadio(page).check({ force: true })
    await expect(row(page, 1)).toBeVisible()
    await page.getByRole('button', { name: 'Next page' }).first().click()
    await expect(row(page, 41)).toBeVisible()
    await row(page, 55).locator('.review-idx').click()
    await expect(row(page, 55)).toHaveAttribute('aria-current', 'true')
    await expect.poll(() => kept(page)).toContain('"view":"lines"')
    await leave(page)

    await page.goto('/#/drama/3')
    await expect(queueRadio(page)).toBeChecked()
    await expect(row(page, 24)).toHaveAttribute('aria-current', 'true')
    await expect(page.locator('.review-line .review-idx')).toHaveText(['#24', '#7', '#12'])
    await allRadio(page).check({ force: true })
    await expect(row(page, 55)).toHaveAttribute('aria-current', 'true')
    await expect(pageNumber(page)).toHaveValue('2')
    await expect(status(page)).toHaveText('Back where you stopped, on line 55.')
    await expectInView(page, 55)
  })

  test('the queue reopens on the card it was left on while that card is listed, else on its first', async ({ page }) => {
    const ids = seedPlaceLines()
    await page.goto('/#/drama/3/review')
    await expect(row(page, 24)).toHaveAttribute('aria-current', 'true')
    await row(page, 7).locator('.review-idx').click()
    await expect(row(page, 7)).toHaveAttribute('aria-current', 'true')
    await expect.poll(() => kept(page)).toContain('"view":"queue"')
    await leave(page)

    await page.goto('/#/drama/3/review')
    await expect(queueRadio(page)).toBeChecked()
    await expect(row(page, 7)).toHaveAttribute('aria-current', 'true')
    await expect(status(page)).toHaveText('Back where you stopped, on line 7.')
    await leave(page)

    // Checked meanwhile (another tab, another device): it has left the queue.
    const checked = await page.request.post(`/api/lines/dramas/3/lines/${ids[6]}/reviewed`, { data: { reviewed: true } })
    expect(checked.ok()).toBe(true)
    await page.goto('/#/drama/3/review')
    await expect(page.locator('.review-line .review-idx')).toHaveText(['#24', '#12'])
    await expect(row(page, 24)).toHaveAttribute('aria-current', 'true')
    await expect(status(page)).toHaveText('')
  })
})

test('the line the page opens on by itself is not kept', async ({ page }) => {
  seedPlaceLines()
  await page.goto('/#/drama/3/review')
  await expect(realRow(page)).toBeVisible()
  await expect(row(page, 12)).toHaveAttribute('aria-current', 'true')
  await leave(page)
  expect(await kept(page)).toBeNull()
})
