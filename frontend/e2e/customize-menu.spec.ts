import { expect, test } from '@playwright/test'

import { expectDiscoverGone, expectLockedItemsCannotBeSwitchedOff, hideDiscoverFromSettings } from './customizeMenu'
import { navLink, openSettingsGroups } from './settingsNav'

// Settings > Menu items on the wide screen (left rail), against the seeded API (auth off).

test('hiding Find removes it from the rail, the address still loads and the choice survives a reload', async ({ page }) => {
  await hideDiscoverFromSettings(page)
  await expectDiscoverGone(page)

  await page.goto('/#/discover')
  await expect(page.getByRole('navigation', { name: 'Find & Add' }).getByRole('link', { name: 'Discover' })).toHaveAttribute('aria-current', 'page')
  await expect(page.getByRole('heading', { name: 'Find on official platforms' })).toBeVisible()
  await page.reload()
  await expectDiscoverGone(page)

  // Switching it back on restores the link.
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'My account and look')
  await page.getByRole('region', { name: 'Menu items' }).getByRole('switch', { name: 'Find & Add' }).click()
  await expect(navLink(page, 'Find & Add')).toBeVisible()
})

test('Library and Settings are listed but locked on', async ({ page }) => {
  await expectLockedItemsCannotBeSwitchedOff(page)
})
