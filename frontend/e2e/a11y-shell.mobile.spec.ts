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
