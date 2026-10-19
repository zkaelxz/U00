import { expect, test } from '@playwright/test'
import { shot } from './shots'

// Every page sits in one column, up to 1440px, so wider monitors add gutters instead of stretching some pages and not others.
const PAGES = ['#/', '#/jobs', '#/sources', '#/discover', '#/settings', '#/translate', '#/library-tools', '#/diagnostics', '#/manga']
const WIDTHS = [1280, 1600, 1920]
const COLUMN_MAX = 1440
const RAIL_MAX = 240 // 15rem expanded rail at 16px root

for (const width of WIDTHS) {
  test.describe(`desktop ${width}px`, () => {
    test.use({ viewport: { width, height: 900 } })

    for (const hash of PAGES) {
      test(`page ${hash}`, async ({ page }) => {
        await page.goto(`/${hash}`)
        await expect(page.locator('.app-main')).toBeVisible()
        await page.waitForLoadState('networkidle')
        const shell = (await page.locator('[data-page-shell]').boundingBox())!.width
        expect(shell).toBeLessThanOrEqual(COLUMN_MAX)
        // Takes the column when the viewport has room for it.
        expect(shell).toBeGreaterThanOrEqual(Math.min(width - RAIL_MAX, COLUMN_MAX) - 100)
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
        expect(overflow).toBeLessThanOrEqual(0)
        const name = hash.replace(/[^a-z]/g, '') || 'library'
        await shot(page, `wide-${width}-${name}`)
      })
    }

    test('library grid fills the column, cards keep their width range', async ({ page }) => {
      await page.goto('/')
      const cards = page.locator('.drama-grid:not(.skeleton) > li')
      await expect(cards.first()).toBeVisible()
      const grid = await page.locator('.drama-grid:not(.skeleton)').boundingBox()
      const card = await cards.first().boundingBox()
      expect(grid!.width).toBeGreaterThan(Math.min(width - RAIL_MAX, COLUMN_MAX) - 100)
      expect(card!.width).toBeGreaterThanOrEqual(299)
      expect(card!.width).toBeLessThanOrEqual(520)
    })
  })
}

test.describe('desktop 1440px library row cards', () => {
  test.use({ viewport: { width: 1440, height: 900 } })

  test('tile stays small and the text column keeps its room', async ({ page }) => {
    await page.goto('/')
    const card = page.locator('.drama-grid:not(.skeleton) > li').first()
    await expect(card).toBeVisible()
    const tile = await card.locator('.drama-tile').boundingBox()
    const main = await card.locator('.drama-card-main').boundingBox()
    expect(tile!.height).toBeLessThanOrEqual(100)
    expect(main!.width).toBeGreaterThanOrEqual(200)
  })
})
