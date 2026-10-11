import { expect, test } from '@playwright/test'

import { expectDiscoverGone, expectLockedItemsCannotBeSwitchedOff, hideDiscoverFromSettings } from './customizeMenu'
import { navLink, openSettingsGroups } from './settingsNav'

// Settings > Customize menu on the wide screen (left rail), against the seeded API (auth off).

test('hiding Discover removes it from the rail, the address still loads and the choice survives a reload', async ({ page }) => {
  await hideDiscoverFromSettings(page)
  await expectDiscoverGone(page)

  await page.goto('/#/discover')
  await expect(page.getByRole('region', { name: 'Discover' }).or(page.getByRole('heading', { name: 'Discover' })).first()).toBeVisible()
  await page.reload()
  await expectDiscoverGone(page)

  // Switching it back on restores the link.
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'Preferences')
  await page.getByRole('region', { name: 'Customize menu' }).getByRole('switch', { name: 'Discover' }).click()
  await expect(navLink(page, 'Discover')).toBeVisible()
})

test('Library and Settings are listed but locked on', async ({ page }) => {
  await expectLockedItemsCannotBeSwitchedOff(page)
})
