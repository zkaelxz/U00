import { expect as baseExpect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

import { mockImports } from './sourcesImportMocks'
import { SOURCES, mockSources } from './sourcesMocks'

// Browser-only sources (needs_browser): Search leaves them out and chapter Import is off,
// with the browser install notice, while playwright or a browser is missing. Every call is mocked.
const expect = baseExpect.configure({ timeout: 15_000 })
test.describe.configure({ timeout: 90_000 })

const READY = { playwright: true, browser_found: true }

async function setup(page: Page, readiness: Record<string, boolean>) {
  const s = await mockSources(page)
  await mockImports(page, s)
  const rows = SOURCES.map((x) => (x.name === 'alpha' ? { ...x, needs_browser: true } : x))
  await page.route(/\/api\/sources(\?.*)?$/, (r) => r.fulfill({ json: rows }))
  await page.route('**/api/setup/readiness', (r) => r.fulfill({ json: readiness }))
  return s
}

test('Search lists a browser-only source when the browser is ready', async ({ page }) => {
  await setup(page, READY)
  await page.goto('/#/sources')
  await expect(page.getByText('Alpha Comics', { exact: true })).toBeVisible()
  await expect(page.getByTestId('needs-browser_package')).toHaveCount(0)
})

test('Search leaves a browser-only source out and says what is missing', async ({ page }) => {
  await setup(page, { playwright: false, browser_found: false })
  await page.goto('/#/sources')
  await expect(page.getByTestId('needs-browser_package')).toBeVisible()
  await expect(page.getByText('Alpha Comics', { exact: true })).toHaveCount(0)
  await expect(page.getByText('Beta Novels', { exact: true })).toBeVisible()
})

test('chapter Import is off for a browser-only source until a browser is found', async ({ page }) => {
  await setup(page, { playwright: true, browser_found: false })
  await page.goto('/#/sources')
  await page.getByRole('radio', { name: 'Paste a link' }).check()
  await page.getByRole('textbox', { name: 'Paste a link' }).fill('https://alpha.example/a/c2')
  await page.getByRole('button', { name: 'Preview' }).click()
  await page.getByRole('article', { name: 'Link preview' }).getByRole('button', { name: 'Open series' }).click()
  const panel = page.getByRole('region', { name: 'Series' })
  await expect(panel.getByText('Alpha Comics · 124 chapters · Ongoing · Chinese')).toBeVisible()
  await expect(panel.getByTestId('needs-browser_program')).toBeVisible()
  await expect(panel.getByRole('combobox', { name: 'Import into' })).toHaveCount(0)
})
