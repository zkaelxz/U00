import type { Page } from '@playwright/test'

// The seeded dramas have no lines, so Translate and Export are disabled with a
// reason. Specs that need to press them pass the real config/readiness through
// with a line count on top.

export async function withTranslateLines(page: Page, id = 1, lines = 3, untranslated = lines) {
  await page.route(`**/api/translate-run/dramas/${id}/config`, async (route) => {
    const real = await (await route.fetch()).json()
    await route.fulfill({ json: { ...real, line_count: lines, untranslated_count: untranslated } })
  })
}

export async function withExportLines(page: Page, id = 1, lines = 3) {
  await page.route(`**/api/export/dramas/${id}/readiness`, async (route) => {
    const real = await (await route.fetch()).json()
    await route.fulfill({ json: { ...real, total_lines: lines } })
  })
}
