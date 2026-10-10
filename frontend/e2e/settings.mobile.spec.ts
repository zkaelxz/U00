import { expect, test } from '@playwright/test'
import { openSettingsGroups } from './settingsNav'
import { hitHeight, installHitArea } from './hitArea'

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

// Phone: each Settings switch has a >=44px-tall hit area (the ::after overlay)
// and the page has no sideways scroll.
test('settings switches have 44px touch targets on a phone', async ({ page }) => {
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'System')
  for (const tab of ['System', 'Preferences'] as const) {
    await openSettingsGroups(page, tab)
    if (tab === 'Preferences') await expect(page.getByRole('switch', { name: 'Extension bridge' })).toBeVisible() // the seeded API leaves the bridge off or on; either way it is a switch
    const switches = page.locator('.settings-panel:not([hidden])').getByRole('switch')
    for (const sw of await switches.all()) {
      // elementFromPoint only sees the viewport; centre it so the +-21px probes stay on screen.
      await sw.evaluate((el) => el.scrollIntoView({ block: 'center' }))
      const hit = await sw.evaluate((el) => {
        const r = el.getBoundingClientRect()
        const cx = r.left + r.width / 2
        const cy = r.top + r.height / 2
        // A probe past the end of the page can't be hit-tested: the last switch sits at the bottom edge.
        const at = (y: number) => y >= window.innerHeight || el.contains(document.elementFromPoint(cx, y))
        return { top: at(cy - 21), bottom: at(cy + 21), width: r.width }
      })
      expect(hit).toEqual({ top: true, bottom: true, width: 44 })
    }
  }
  await openSettingsGroups(page, 'Translation and keys')
  // "Test" and "Set key" buttons in the engine rows are dense (.btn-sm) but still 44px on touch.
  for (const b of await page.getByRole('region', { name: 'Which engine does what' }).getByRole('button', { name: /^(Test|Set key|Replace|Close) / }).all())
    expect((await hitHeight(b))).toBeGreaterThanOrEqual(44)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})

for (const width of [360, 390]) {
  test(`phone ${width}: tabs are a 3-column grid of 44px buttons and search spans the width`, async ({ page }) => {
    await page.setViewportSize({ width, height: 800 })
    await page.goto('/#/settings')
    const tabs = page.getByRole('tab')
    await expect(tabs).toHaveCount(3)
    const boxes = await Promise.all((await tabs.all()).map((t) => t.boundingBox()))
    for (const b of boxes) expect(b!.height).toBeGreaterThanOrEqual(44)
    expect(new Set(boxes.map((b) => Math.round(b!.y))).size).toBe(1) // one row, three columns
    const search = await page.getByRole('searchbox', { name: 'Search settings' }).boundingBox()
    const list = await page.getByRole('tablist').boundingBox()
    expect(Math.abs(search!.width - list!.width)).toBeLessThan(2)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    expect(await page.evaluate(() => { const l = document.querySelector('[role=tablist]')!; return l.scrollWidth <= l.clientWidth })).toBe(true)
  })
}
