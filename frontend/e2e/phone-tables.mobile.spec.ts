import { expect, test, type Locator, type Page } from '@playwright/test'

import { installHitArea } from './hitArea'
import { mockPhoneTables } from './phoneTablesMocks'

// Phone: the Characters and Glossary tables are stacked cards and Bulk batches the stacked list. Nothing scrolls
// sideways, every field and the Save and Edit buttons are on screen and answer a tap, names stay accessible.

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
  await mockPhoneTables(page)
})
test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

const sizes = [{ width: 390, height: 844 }, { width: 360, height: 800 }]

async function openSection(page: Page, title: string) {
  const summary = page.locator('summary').filter({ has: page.locator('.section-title', { hasText: new RegExp(`^${title}$`) }) }).first()
  if ((await summary.locator('xpath=..').getAttribute('open')) === null) await summary.click()
}

// On screen horizontally and the topmost thing at its centre (so nothing overlaps it).
async function expectTappable(loc: Locator, min = 44) {
  await loc.scrollIntoViewIfNeeded()
  await expect(loc).toBeVisible()
  const { inside, hit } = await loc.evaluate((e) => {
    const r = e.getBoundingClientRect()
    const at = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)
    return { inside: r.left >= 0 && r.right <= window.innerWidth, hit: !!at && (e === at || e.contains(at) || at.contains(e)) }
  })
  expect(inside, 'inside the viewport width').toBe(true)
  expect(hit, 'hit-testable').toBe(true)
  expect(await loc.evaluate((e) => window.hitHeight(e))).toBeGreaterThanOrEqual(min)
}

async function noSideways(region: Locator) {
  const m = await region.evaluate((el) => ({
    innerScroll: Array.from(el.querySelectorAll('.table-scroll, table')).some((t) => t.scrollWidth > t.clientWidth),
    poking: Array.from(el.querySelectorAll('input, select, button, textarea')).filter((c) => c.getBoundingClientRect().right > el.getBoundingClientRect().right + 1).map((c) => c.getAttribute('aria-label') ?? c.textContent),
    docScroll: document.documentElement.scrollWidth, inner: window.innerWidth,
  }))
  expect(m.innerScroll, 'inner scroller overflows').toBe(false)
  // Nothing pokes out of the region's right edge (a wider grid column would, without an inner scroller to show it).
  expect(m.poking, 'fields past the region edge').toEqual([])
  expect(m.docScroll).toBeLessThanOrEqual(m.inner)
}

for (const size of sizes) {
  test.describe(`${size.width}px`, () => {
    test.use({ viewport: size })

    test('characters cards', async ({ page }) => {
      await page.goto('/#/drama/1/translate')
      await page.waitForLoadState('networkidle')
      await openSection(page, 'Characters')
      const region = page.getByRole('region', { name: 'Characters' })
      await expect(region.getByLabel('Name for SPEAKER_00', { exact: true })).toBeVisible()
      await noSideways(region)
      for (const label of ['SPEAKER_00', 'SPEAKER_01']) {
        for (const name of ['Name for', 'Gender for']) await expectTappable(region.getByLabel(`${name} ${label}`, { exact: true }), 40)
      }
      await expect(region.getByLabel('Custom pronouns for SPEAKER_01')).toBeVisible()
      // Labels come from the cells; the header row is no longer painted.
      await expect(region.locator('td[data-label="Lines"]').first()).toHaveText('128')
      const label = await region.locator('td[data-label="Lines"]').first().evaluate((e) => getComputedStyle(e, '::before').content)
      expect(label).toBe('"Lines"')
      await expect(region.getByRole('row')).toHaveCount(5) // header + 2 speakers x (card + rename row)
      // Save is enabled once something changes, and answers a tap.
      const save = region.getByRole('row').filter({ has: page.getByLabel('Name for SPEAKER_01', { exact: true }) }).getByRole('button', { name: 'Save' })
      await expect(save).toBeDisabled()
      await region.getByLabel('Name for SPEAKER_01', { exact: true }).fill('Mei')
      await expect(save).toBeEnabled()
      await expectTappable(save)
      // The rename form lives in the same card and stays inside the viewport.
      await region.getByRole('button', { name: 'Rename speaker' }).first().click()
      await expectTappable(region.getByLabel('New name for SPEAKER_00', { exact: true }), 40)
      await noSideways(region)
    })

    test('glossary cards', async ({ page }) => {
      await page.goto('/#/drama/1/translate')
      await page.waitForLoadState('networkidle')
      await openSection(page, 'Glossary')
      const region = page.getByRole('region', { name: 'Glossary' })
      await expect(region.getByRole('button', { name: 'Edit 林晚' })).toBeVisible()
      await noSideways(region)
      for (const t of ['林晚', '玄天宗']) {
        await expectTappable(region.getByRole('button', { name: `Edit ${t}` }))
        await expectTappable(region.getByLabel(`Select ${t}`), 24)
      }
      // The tick box sits in a 44px tap area.
      const cell = await region.getByLabel('Select 林晚').evaluate((e) => e.closest('td')!.getBoundingClientRect().width)
      expect(cell).toBeGreaterThanOrEqual(44)
      await expect(region.getByRole('cell', { name: 'Lin Wan' })).toBeVisible()
      await expect(region.locator('td[data-label="Aliases"]').first()).toHaveText('晚晚, 小晚')
      await region.getByRole('button', { name: 'Edit 林晚' }).click()
      await expect(page.getByLabel('Original').first()).toBeVisible()
    })

    test('bulk batches list', async ({ page }) => {
      await page.goto('/#/drama/1/translate')
      await page.waitForLoadState('networkidle')
      const panel = page.getByRole('region', { name: 'Bulk batches' })
      await expect(panel.locator('.bulk-list li')).toHaveCount(2)
      await expect(panel.locator('table')).toHaveCount(0)
      await noSideways(panel)
      await panel.getByRole('button', { name: 'Cancel batch 21' }).click()
      await expectTappable(panel.getByRole('button', { name: 'Yes, cancel it' }))
      await noSideways(panel)
      const wrap = await panel.locator('.badge').first().evaluate((e) => getComputedStyle(e).whiteSpace)
      expect(wrap).toBe('normal')
    })
  })
}
