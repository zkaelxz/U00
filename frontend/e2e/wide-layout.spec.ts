import { expect, test } from '@playwright/test'

// List/card pages use the 1800px column; form pages keep 1200px (index.css, data-width on .app-main).
// Screenshots go to the git-ignored tmp/scratchpad, not the repo.
const WIDE_PAGES = ['#/', '#/library-tools', '#/jobs', '#/sources', '#/discover', '#/diagnostics', '#/manga']
const NARROW_PAGES = ['#/settings', '#/translate']
const WIDTHS = [1280, 1600, 1920]
const RAIL_MAX = 240 // 15rem expanded rail at 16px root

for (const width of WIDTHS) {
  test.describe(`desktop ${width}px`, () => {
    test.use({ viewport: { width, height: 900 } })

    for (const [kind, pages] of [['wide', WIDE_PAGES], ['narrow', NARROW_PAGES]] as const) {
      for (const hash of pages) {
        test(`${kind} page ${hash}`, async ({ page }) => {
          await page.goto(`/${hash}`)
          const main = page.locator('.app-main')
          await expect(main).toBeVisible()
          await page.waitForLoadState('networkidle')
          const w = (await main.boundingBox())!.width
          const available = width - 56 // at least the collapsed rail
          if (kind === 'wide') {
            expect(w).toBeLessThanOrEqual(1800)
            // Takes whatever the viewport offers up to the cap; never the old 1200 limit when more is free.
            expect(w).toBeGreaterThanOrEqual(Math.min(available - RAIL_MAX + 56, 1800) - 1)
          } else {
            expect(w).toBeLessThanOrEqual(1200)
          }
          const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
          expect(overflow).toBeLessThanOrEqual(0)
          const name = hash.replace(/[^a-z]/g, '') || 'library'
          await page.screenshot({ path: `../tmp/scratchpad/wide-${width}-${name}.png` })
        })
      }
    }

    test('library grid gains columns, cards keep their width range', async ({ page }) => {
      await page.goto('/')
      const cards = page.locator('.drama-grid > li')
      await expect(cards.first()).toBeVisible()
      const grid = await page.locator('.drama-grid').boundingBox()
      const card = await cards.first().boundingBox()
      expect(grid!.width).toBeGreaterThan(Math.min(width - RAIL_MAX, 1800) - 80)
      expect(card!.width).toBeGreaterThanOrEqual(299)
      expect(card!.width).toBeLessThanOrEqual(520)
    })
  })
}

test.describe('desktop 1440px library row cards', () => {
  test.use({ viewport: { width: 1440, height: 900 } })

  test('tile stays small and the text column keeps its room', async ({ page }) => {
    await page.goto('/')
    const card = page.locator('.drama-grid > li').first()
    await expect(card).toBeVisible()
    const tile = await card.locator('.drama-tile').boundingBox()
    const main = await card.locator('.drama-card-main').boundingBox()
    expect(tile!.height).toBeLessThanOrEqual(100)
    expect(main!.width).toBeGreaterThanOrEqual(200)
  })
})
