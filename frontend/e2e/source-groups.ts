import type { Page } from '@playwright/test'

// Opens a Source stage group Section if it is closed (its state is remembered,
// so a plain click could just as well collapse it).
export async function openGroup(page: Page, title: string) {
  const group = page.locator('details.section', { has: page.locator('summary > .section-title', { hasText: new RegExp(`^${title}$`) }) }).first()
  if ((await group.getAttribute('open')) === null) await group.locator('summary').first().click()
}
