import type { Page } from '@playwright/test'

// The four media export blocks fold under their headings, and only the subtitle-track video starts open.
// Specs that start a job in a block call this first, so they do not depend on the defaults.
export async function openExportBlocks(page: Page): Promise<void> {
  const folded = page.locator('.export-block-toggle[aria-expanded="false"]')
  while ((await folded.count()) > 0) await folded.first().click()
}
