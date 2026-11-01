import { expect, test } from './fixtures'

// Phone project (touch). The header brand stays whole at the narrowest widths, and the small controls
// the audit named keep a 44px hit area.

for (const width of [360, 390]) {
  test(`brand is not clipped and the header does not scroll sideways at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 800 })
    await page.goto('/#/')
    const brand = page.locator('.app-brand a')
    const box = (await brand.boundingBox())!
    expect(box.width).toBeGreaterThanOrEqual(44)
    expect(box.height).toBeGreaterThanOrEqual(44)
    const clipped = await brand.evaluate((a) => a.scrollWidth > a.clientWidth)
    expect(clipped).toBe(false)
    // Below 400px the word gives way to the B mark, which is visible; the link keeps its full name.
    await expect(page.locator('.app-brand .rail-brand-mark')).toBeVisible()
    await expect(brand).toHaveAccessibleName('Baihe Studio: Library')
    const sideways = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    expect(sideways).toBeLessThanOrEqual(0)
  })
}

test('the help (i) button has a 44px hit area', async ({ page }) => {
  await page.goto('/#/settings')
  const help = page.locator('.field-help-btn').first()
  await help.scrollIntoViewIfNeeded()
  const b = (await help.boundingBox())!
  // The button itself stays small; the hit area is the ::after outset, so points 21px away on every side still hit it.
  expect(b.width).toBeLessThan(30)
  const hits = await help.evaluate((el) => {
    const r = el.getBoundingClientRect()
    const cx = r.left + r.width / 2
    const cy = r.top + r.height / 2
    return [[0, -21], [0, 21]].map(([dx, dy]) => el.contains(document.elementFromPoint(cx + dx, cy + dy)))
  })
  expect(hits).toEqual([true, true])
})

test('a radio or checkbox row is a 44px target', async ({ page }) => {
  await page.goto('/#/drama/3')
  const row = page.locator('label.check:has(input[type="radio"])').first()
  await row.waitFor({ state: 'attached' })
  await page.evaluate(() => document.querySelectorAll('details').forEach((d) => { d.open = true }))
  await expect(row).toBeVisible()
  const box = (await row.boundingBox())!
  expect(box.height).toBeGreaterThanOrEqual(44)
})

// Large text on a phone (Android's text size, approximated by the root font size) and the narrowest 320px screen:
// nothing may push the page sideways. A select is as wide as its longest option and a fixed-width grid column
// doesn't shrink, so either one used to.
const sideways = (page: import('@playwright/test').Page) =>
  page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
const bigText = (page: import('@playwright/test').Page, size: string) =>
  page.addInitScript((s) => document.addEventListener('DOMContentLoaded', () => { document.documentElement.style.fontSize = s }), size)

test('Settings does not scroll sideways at 320px', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 800 })
  await page.goto('/#/settings')
  await expect(page.locator('.routing-tasks select').first()).toBeVisible()
  expect(await sideways(page)).toBeLessThanOrEqual(0)
})

for (const [hash, ready] of [
  ['#/settings', '.routing-tasks select'],
  ['#/sources', '.sources-search-in .toggle'],
  ['#/translate', '.translate-source textarea'],
  ['#/drama/3/export', '.export-block'],
  ['#/drama/2', '.actions .btn'],
] as const) {
  test(`${hash} does not scroll sideways with large text`, async ({ page }) => {
    await page.setViewportSize({ width: 412, height: 915 })
    await bigText(page, '200%')
    await page.goto(`/${hash}`)
    await expect(page.locator(ready).first()).toBeAttached()
    await page.evaluate(() => document.querySelectorAll('details').forEach((d) => { d.open = true }))
    // Only the page's own content: the fixed bottom bar follows the layout viewport, which page overflow widens.
    const over = await page.evaluate(() => {
      const textRight = (el: Element) => {
        const range = document.createRange()
        range.selectNodeContents(el)
        return range.getBoundingClientRect().right
      }
      const w = document.documentElement.clientWidth
      return [...document.querySelectorAll('main *')]
        .filter((el) => !el.closest('[class*="scroll"], .visually-hidden') &&
          // A box inside the screen whose own text still runs out of it (a button that doesn't wrap) counts too.
          (el.getBoundingClientRect().right > w + 1 || (el.matches('.btn') && textRight(el) > el.getBoundingClientRect().right + 1)))
        .map((el) => el.className)
    })
    expect(over).toEqual([])
  })
}

test('stage tab labels keep their first letters with large text', async ({ page }) => {
  await page.setViewportSize({ width: 412, height: 915 })
  await bigText(page, '200%')
  await page.goto('/#/drama/3/export')
  const tabs = page.locator('.stage-tabs a')
  await expect(tabs).toHaveCount(4)
  // A label too long for its tab keeps its first letters (and ends in "…"): its text never starts left of the tab.
  const cutAtStart = await tabs.evaluateAll((as) => as.filter((a) => {
    const range = document.createRange()
    range.selectNodeContents(a.querySelector('.stage-label')!)
    return range.getBoundingClientRect().left < a.getBoundingClientRect().left - 1
  }).length)
  expect(cutAtStart).toBe(0)
})

test('a phone held sideways lets the title strip scroll away so Review has room for lines', async ({ page }) => {
  await page.setViewportSize({ width: 915, height: 412 })
  await page.goto('/#/drama/3/review')
  const strip = page.locator('.ws-strip')
  await expect(strip).toBeVisible()
  // The seeded title has no lines; a tall page stands in for a long Review list.
  await page.evaluate(() => { document.querySelector<HTMLElement>('.workspace')!.style.minHeight = '3000px'; window.scrollTo(0, 400) })
  await expect.poll(async () => (await strip.boundingBox())?.y ?? 0).toBeLessThan(0)
})

test('the reader names the title in its one h1', async ({ page }) => {
  await page.goto('/#/read/2')
  await expect(page.getByRole('heading', { level: 1 })).toHaveText("Heaven Official's Blessing")
  await expect(page).toHaveTitle("Heaven Official's Blessing · Baihe Studio")
})
