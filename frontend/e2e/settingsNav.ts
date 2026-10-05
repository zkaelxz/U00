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

// Settings, Admin, Diagnostics and Assistant: a left-rail link from 1024px up, the header cogwheel menu below it.
const gearMenu = (page: Page): Locator => page.getByRole('group', { name: 'Settings and tools pages' })
const railNav = (page: Page): Locator => page.getByRole('navigation', { name: 'Main' })
export const gearLink = (page: Page, name: string): Locator =>
  railNav(page).getByRole('link', { name, exact: true }).or(gearMenu(page).getByRole('link', { name, exact: true }))

export async function openGear(page: Page) {
  const summary = page.locator('summary[aria-label="Settings and tools"]')
  // No cogwheel on a wide screen: the links are always in the rail.
  if ((await summary.count()) === 0) return
  if (!(await summary.evaluate((el) => (el.parentElement as HTMLDetailsElement).open))) await summary.click()
}
