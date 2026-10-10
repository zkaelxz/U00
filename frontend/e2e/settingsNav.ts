import type { Locator, Page } from '@playwright/test'

export type SettingsTabName = 'Translation and keys' | 'Preferences' | 'System'

// Settings shows one tab at a time (the choice is remembered per browser); a spec for a card opens the tab that holds it.
export async function openSettingsGroups(page: Page, tab: SettingsTabName) {
  const button = page.getByRole('tab', { name: tab })
  // Members see no tab row at all (they get Preferences only); the tabs also wait for the settings call.
  if ((await button.waitFor({ state: 'visible', timeout: 4000 }).then(() => true, () => false))) {
    await button.click()
    await page.locator('.settings-panel:not([hidden])').waitFor()
  }
  // A card's help icon can land under the pointer, which opens its tooltip.
  await page.mouse.move(0, 0)
}

// Every page link lives in the same registry-driven <nav aria-label="Main">: always in the rail from 1024px up, inside the
// drawer below it (only while the drawer is open).
export const navLink = (page: Page, name: string): Locator =>
  page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name, exact: true })

// Opens the drawer when there is a Menu button (below 1024px); a no-op beside the always-visible rail.
export async function openMenu(page: Page) {
  // The header (and with it the Menu button) only renders once the session has answered.
  await page.locator('.app-header').waitFor()
  const button = page.getByRole('button', { name: 'Menu', exact: true })
  if ((await button.count()) === 0) return
  if ((await button.getAttribute('aria-expanded')) !== 'true') await button.click()
  await page.getByRole('dialog', { name: 'Main menu' }).waitFor()
}
