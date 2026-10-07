import { expect, type Page } from '@playwright/test'

// The four Benchmark Lab sections fold under their headings and only the golden sets start open.
// Specs that work inside a card call this first, so they do not depend on the defaults.
export async function openBenchSections(page: Page): Promise<void> {
  await expect(page.locator('.bench-section-toggle')).toHaveCount(4)
  const folded = page.locator('.bench-section-toggle[aria-expanded="false"]')
  while ((await folded.count()) > 0) await folded.first().click()
}
