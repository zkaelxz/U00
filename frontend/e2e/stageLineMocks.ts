import type { Page } from '@playwright/test'

// The seeded dramas have no lines, so Translate and Export are disabled with a
// reason. Specs that need to press them pass the real config/readiness through
// with a line count on top.

// A route handler that proxies to the real API (route.fetch) can still be in
// flight when the test ends; Playwright then rejects it with "Test ended" or a
// closed-target error. That is harmless, so drop it; any other error still fails.
export async function untilTestEnds(fn: () => Promise<void>) {
  try {
    await fn()
  } catch (e) {
    if (!/Test ended|Target page, context or browser has been closed|has been disposed/.test(String(e))) throw e
  }
}

export async function withTranslateLines(page: Page, id = 1, lines = 3, untranslated = lines) {
  await page.route(`**/api/translate-run/dramas/${id}/config`, (route) =>
    untilTestEnds(async () => {
      const real = await (await route.fetch()).json()
      await route.fulfill({ json: { ...real, line_count: lines, untranslated_count: untranslated } })
    }),
  )
}

export async function withExportLines(page: Page, id = 1, lines = 3) {
  await page.route(`**/api/export/dramas/${id}/readiness`, (route) =>
    untilTestEnds(async () => {
      const real = await (await route.fetch()).json()
      await route.fulfill({ json: { ...real, total_lines: lines } })
    }),
  )
}

// The real API refuses ASS for a title with no translated lines (409), so the
// lines withExportLines pretends to have are given a header-only script, named
// as the server names it (`filename`); any other answer (a refused style, a
// server error) is passed through.
const ASS_SCRIPT = '[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\n\n[Events]\n'

export async function withExportAss(page: Page, id = 1, filename = '') {
  await page.route(`**/api/export/dramas/${id}/ass`, (route) =>
    untilTestEnds(async () => {
      const real = await route.fetch()
      if (real.status() !== 409) return route.fulfill({ response: real })
      await route.fulfill({
        status: 200, contentType: 'text/x-ssa; charset=utf-8', body: ASS_SCRIPT,
        headers: filename ? { 'content-disposition': `attachment; filename="${filename}"` } : {},
      })
    }),
  )
}
