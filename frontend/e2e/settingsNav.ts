import type { Locator, Page } from '@playwright/test'
import { pickTab } from './tabPicker'

export type SettingsTabName =
  | 'Translation and keys'
  | 'Costs'
  | 'Notifications and backups'
  | 'Sharing and devices'
  | 'Integrations'
  | 'System'

// Settings shows one tab at a time (the choice is remembered per browser); a spec for a card opens the tab that holds it.
// It also presses Expand all, so a spec can use any card of the tab however the tab starts; the starting
// state and the remembered state are covered by settings-tabs.spec.ts.
export async function openSettingsGroups(page: Page, tab: SettingsTabName) {
  // Members see no tab row at all (they get the Sharing and devices cards only); the tabs (a picker on a phone) also wait for the settings call.
  const bar = page.getByRole('tab', { name: tab, exact: true }).or(page.locator('.ui-tab-picker-btn'))
  if ((await bar.first().waitFor({ state: 'visible', timeout: 4000 }).then(() => true, () => false))) {
    await pickTab(page, tab, 'Settings')
    await page.locator('.settings-panel:not([hidden])').waitFor()
  }
  // On a phone the two buttons sit in a small menu.
  const expandAll = page.getByRole('button', { name: 'Expand all' })
  if (!(await expandAll.isVisible())) await page.getByText('Expand / collapse', { exact: true }).click()
  await expandAll.click()
  // A card's help icon can land under the pointer, which opens its tooltip.
  await page.mouse.move(0, 0)
}

// Every page link lives in the same registry-driven <nav aria-label="Main">: always in the rail from 1024px up, inside the
// drawer below it (only while the drawer is open).
export const navLink = (page: Page, name: string): Locator =>
  page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name, exact: true })

// Opens the drawer when there is a Menu button (the header's) or a More button (the phone bar), below 600px; a no-op beside the always-visible dock.
export async function openMenu(page: Page) {
  // The header (and with it the Menu button) only renders once the session has answered.
  await page.locator('.app-header').waitFor()
  const button = page.getByRole('button', { name: /^(Menu|More)$/ })
  if ((await button.count()) === 0) return
  if ((await button.getAttribute('aria-expanded')) !== 'true') await button.click()
  await page.getByRole('dialog', { name: 'Main menu' }).waitFor()
}
