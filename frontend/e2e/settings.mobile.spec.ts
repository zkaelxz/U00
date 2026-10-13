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
  for (const tab of ['Translation and keys', 'Costs', 'Notifications and backups', 'Sharing and devices', 'Integrations', 'System'] as const) {
    await openSettingsGroups(page, tab)
    if (tab === 'Integrations') await expect(page.getByRole('switch', { name: 'Extension bridge' })).toBeVisible() // the seeded API leaves the bridge off or on; either way it is a switch
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
  test(`phone ${width}: the six tabs are 44px buttons that wrap between words, and search spans the width`, async ({ page }) => {
    await page.setViewportSize({ width, height: 800 })
    await page.goto('/#/settings')
    const tabs = page.getByRole('tab')
    await expect(tabs).toHaveCount(6)
    const list = await page.getByRole('tablist').boundingBox()
    for (const t of await tabs.all()) {
      const b = (await t.boundingBox())!
      expect(b.height).toBeGreaterThanOrEqual(44)
      // All six are on screen: a row that wraps instead of scrolling hides none of them.
      expect(b.x).toBeGreaterThanOrEqual(list!.x - 1)
      expect(b.x + b.width).toBeLessThanOrEqual(list!.x + list!.width + 1)
      // A label breaks between words, never inside one: it is no wider than its own content.
      expect(await t.evaluate((el) => el.scrollWidth <= el.clientWidth + 1)).toBe(true)
    }
    const search = await page.getByRole('searchbox', { name: 'Search settings' }).boundingBox()
    expect(Math.abs(search!.width - list!.width)).toBeLessThan(2)
    expect(await page.evaluate(() => { const l = document.querySelector('[role=tablist]')!; return l.scrollWidth <= l.clientWidth + 1 })).toBe(true)
    await page.getByRole('tab', { name: 'System', exact: true }).click()
    await expect(page.getByRole('tab', { name: 'System', exact: true })).toHaveAttribute('aria-selected', 'true')
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  })
}
