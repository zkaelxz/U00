import { expect, test } from '@playwright/test'

import { expectDiscoverGone, expectLockedItemsCannotBeSwitchedOff, hideDiscoverFromSettings } from './customizeMenu'

// Settings > Customize menu on a phone: the drawer follows the choice and the switches keep a 44px target.

test('hiding Discover removes it from the drawer, the address still loads and the choice survives a reload', async ({ page }) => {
  const card = await hideDiscoverFromSettings(page)
  for (const sw of await card.getByRole('switch').all()) {
    await sw.evaluate((el) => el.scrollIntoView({ block: 'center' }))
    expect(await sw.evaluate((el) => { const r = el.getBoundingClientRect(); const cx = r.left + r.width / 2; const cy = r.top + r.height / 2; return el.contains(document.elementFromPoint(cx, cy - 21)) && el.contains(document.elementFromPoint(cx, cy + 21)) })).toBe(true)
  }
  await expectDiscoverGone(page)
  await page.goto('/#/discover')
  await expect(page.getByRole('heading', { name: 'Discover' }).first()).toBeVisible()
  await page.reload()
  await expectDiscoverGone(page)
})

test('Library and Settings are listed but locked on', async ({ page }) => {
  await expectLockedItemsCannotBeSwitchedOff(page)
})
