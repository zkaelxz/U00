import { expect, type Page } from '@playwright/test'

import { navLink, openMenu, openSettingsGroups } from './settingsNav'

// Shared by the desktop (rail) and phone (drawer) specs: hide Discover from Settings > Customize menu.
export async function hideDiscoverFromSettings(page: Page) {
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'Preferences')
  const card = page.getByRole('region', { name: 'Customize menu' })
  await card.getByRole('switch', { name: 'Discover' }).click()
  await expect(card.getByRole('switch', { name: 'Discover' })).toHaveAttribute('aria-checked', 'false')
  return card
}

export async function expectDiscoverGone(page: Page) {
  await openMenu(page)
  await expect(navLink(page, 'Library')).toBeVisible()
  await expect(navLink(page, 'Discover')).toHaveCount(0)
  await expect(navLink(page, 'Sources')).toBeVisible()
}

export async function expectLockedItemsCannotBeSwitchedOff(page: Page) {
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'Preferences')
  const card = page.getByRole('region', { name: 'Customize menu' })
  for (const name of ['Library', 'Settings']) {
    const sw = card.getByRole('switch', { name: name, exact: true })
    await expect(sw).toBeDisabled()
    await expect(sw).toHaveAttribute('aria-checked', 'true')
  }
}
