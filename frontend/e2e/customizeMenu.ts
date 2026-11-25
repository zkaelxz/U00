import { expect, type Page } from '@playwright/test'

import { navLink, openMenu, openSettingsGroups } from './settingsNav'

// Shared by the desktop (rail) and phone (drawer) specs: hide Find from Settings > Menu items.
export async function hideDiscoverFromSettings(page: Page) {
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'My account and look')
  const card = page.getByRole('region', { name: 'Menu items' })
  await card.getByRole('switch', { name: 'Find & Add' }).click()
  await expect(card.getByRole('switch', { name: 'Find & Add' })).toHaveAttribute('aria-checked', 'false')
  return card
}

export async function expectDiscoverGone(page: Page) {
  await openMenu(page)
  await expect(navLink(page, 'Library')).toBeVisible()
  await expect(navLink(page, 'Find & Add')).toHaveCount(0)
  await expect(navLink(page, 'Live')).toBeVisible()
}

export async function expectLockedItemsCannotBeSwitchedOff(page: Page) {
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'My account and look')
  const card = page.getByRole('region', { name: 'Menu items' })
  for (const name of ['Library', 'Settings']) {
    const sw = card.getByRole('switch', { name: name, exact: true })
    await expect(sw).toBeDisabled()
    await expect(sw).toHaveAttribute('aria-checked', 'true')
  }
}
