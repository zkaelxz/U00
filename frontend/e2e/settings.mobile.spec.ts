import { expect, test } from '@playwright/test'
import { openSettingsGroups } from './settingsNav'
import { hitHeight, installHitArea } from './hitArea'
import { expectPickedTab, pickTab } from './tabPicker'

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

// Phone: each Settings switch has a >=44px-tall hit area (the ::after overlay)
// and the page has no sideways scroll.
test('settings switches have 44px touch targets on a phone', async ({ page }) => {
  await page.goto('/#/settings')
  for (const tab of ['Translation and keys', 'Costs', 'Notifications and backups', 'Account and display', 'Integrations', 'System'] as const) {
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
  test(`phone ${width}: the six sections are one 48px picker, and search spans the width`, async ({ page }) => {
    await page.setViewportSize({ width, height: 800 })
    await page.goto('/#/settings')
    await expect(page.getByRole('tablist')).toHaveCount(0)
    const picker = page.getByRole('button', { name: /^Settings: / })
    await expect(picker).toBeVisible()
    const button = (await picker.boundingBox())!
    expect(button.height).toBeGreaterThanOrEqual(44)
    const search = (await page.getByRole('searchbox', { name: 'Search settings' }).boundingBox())!
    expect(Math.abs(search.width - button.width)).toBeLessThan(2)
    await picker.click()
    const options = page.locator('.ui-tab-picker-list').getByRole('option')
    await expect(options).toHaveCount(6)
    for (const option of await options.all()) expect((await option.boundingBox())!.height).toBeGreaterThanOrEqual(44)
    await pickTab(page, 'System', 'Settings')
    await expectPickedTab(page, 'Settings', 'System')
    await expect(page.locator('.ui-tab-picker-list')).toHaveCount(0) // choosing closes the list
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  })
}
