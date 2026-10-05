import type { Locator, Page } from '@playwright/test'

// Settings folds most groups (Integrations, Alerts, Advanced and so on); a spec for a card in one of those opens them all first.
export async function openSettingsGroups(page: Page) {
  // The groups only render once Settings has loaded (and only for an admin).
  await page.locator('.settings-fold > details.section > summary').first().waitFor({ state: 'attached', timeout: 4000 }).catch(() => {})
  for (const summary of await page.locator('.settings-fold > details.section > summary').all()) {
    if (!(await summary.evaluate((el) => (el.parentElement as HTMLDetailsElement).open))) await summary.click()
  }
  // The opened section can land a field's help icon under the pointer, which opens its tooltip.
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
