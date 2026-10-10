import type { Page } from '@playwright/test'

export const PENDING_BASE = '/api/diagnostics/pending-install'

export const EMPTY_STATUS = { packages: [], created: null, before: {}, problem: null, result: null, applying: false }

export const planFor = (packages: string[], o: Record<string, unknown> = {}) => ({
  packages, available: true, mode: 'now', changes: [], summary: [], loaded: [], blocked: [], needs_confirm: [],
  note: null, ...o,
})

/** The preview every install now asks for first: nothing in use, so it installs as before. */
export async function mockPlainPlan(page: Page): Promise<void> {
  await page.route((u) => u.pathname === `${PENDING_BASE}/plan`, (r) => {
    const body = r.request().postDataJSON() as { packages: string[] }
    return r.fulfill({ json: planFor(body.packages) })
  })
}
