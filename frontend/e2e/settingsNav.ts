import type { Locator, Page } from '@playwright/test'

// Settings keeps integrations and experimental options in collapsed
// sections; a spec for one of those cards opens the section first.
export async function openSettingsGroups(page: Page) {
  for (const title of ['Integrations', 'Experimental & developer']) {
    const summary = page.locator('details.section > summary', { hasText: title }).first()
    // The groups only render once Settings has loaded (and only for an admin).
    if (!(await summary.waitFor({ state: 'attached', timeout: 4000 }).then(() => true, () => false))) continue
    if (!(await summary.evaluate((el) => (el.parentElement as HTMLDetailsElement).open))) await summary.click()
  }
  // The opened section can land a field's help icon under the pointer, which opens its tooltip.
  await page.mouse.move(0, 0)
}

// The header cogwheel menu: Settings, Admin, Diagnostics, Assistant.
const gearMenu = (page: Page): Locator => page.getByRole('group', { name: 'Settings and tools pages' })
export const gearLink = (page: Page, name: string): Locator => gearMenu(page).getByRole('link', { name })

export async function openGear(page: Page) {
  const summary = page.locator('summary[aria-label="Settings and tools"]')
  if (!(await summary.evaluate((el) => (el.parentElement as HTMLDetailsElement).open))) await summary.click()
}
