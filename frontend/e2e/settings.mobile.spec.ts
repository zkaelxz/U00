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
  for (const tab of ['Engines and keys', 'Costs', 'Transcription and jobs', 'Connections', 'Updates', 'My account and look', 'Advanced'] as const) {
    await openSettingsGroups(page, tab)
    if (tab === 'Connections') await expect(page.getByRole('switch', { name: 'Extension bridge' })).toBeVisible() // the seeded API leaves the bridge off or on; either way it is a switch
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
  await openSettingsGroups(page, 'Engines and keys')
  // "Test" and "Set key" buttons in the engine rows are dense (.btn-sm) but still 44px on touch.
  for (const b of await page.getByRole('region', { name: 'Which engine does what' }).getByRole('button', { name: /^(Test|Set key|Replace|Close) / }).all())
    expect((await hitHeight(b))).toBeGreaterThanOrEqual(44)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})

for (const width of [360, 390]) {
  test(`phone ${width}: the seven sections are one swipeable row of 44px tabs, one tap each`, async ({ page }) => {
    await page.setViewportSize({ width, height: 800 })
    await page.goto('/#/settings')
    const row = page.getByRole('tablist', { name: 'Settings' })
    await expect(row).toBeVisible()
    await expect(page.locator('.ui-tab-picker-btn')).toHaveCount(0)
    const tabs = row.getByRole('tab')
    await expect(tabs).toHaveCount(7)
    for (const tab of await tabs.all()) expect((await tab.boundingBox())!.height).toBeGreaterThanOrEqual(44)
    // The row scrolls inside itself; the page never scrolls sideways.
    expect(await row.evaluate((el) => el.scrollWidth > el.clientWidth)).toBe(true)
    await row.getByRole('tab', { name: 'Advanced', exact: true }).click()
    await expect(row.getByRole('tab', { name: 'Advanced', exact: true })).toHaveAttribute('aria-selected', 'true')
    await expect(page.locator('.settings-panel:not([hidden])')).toHaveCount(1)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  })
}

// The phone follows its own screen for width, spacing and the nav, but text size and style still apply there.
test('Settings shows only Text size and Style of Display on a phone', async ({ page }) => {
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'My account and look')
  await expect(page.getByLabel('Sharing', { exact: true }).first()).toBeVisible()
  await expect(page.getByLabel('Text size', { exact: true })).toBeVisible()
  await expect(page.getByLabel('Style', { exact: true })).toBeVisible()
  await expect(page.getByLabel('Page width', { exact: true })).toHaveCount(0)
  await expect(page.getByLabel('Spacing', { exact: true })).toHaveCount(0)
  await expect(page.getByLabel('Nav position', { exact: true })).toHaveCount(0)
})
